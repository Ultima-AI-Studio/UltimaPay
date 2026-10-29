# -*- coding: utf-8 -*-
"""
payments.merchant_api —— 外部商户支付 API（UGP-30，产品无关）

- 商户订单使用商户身份 + 外部订单号 + 外部用户引用；支付核心不要求外部用户存在
  Gold users 表，也不硬编码 Gold 套餐（不查 subscription_plans）。
- 商户可用链及收款地址按商户自身配置冻结：未配链直接拒绝，绝不悄悄回落平台资金池；
  无效商户身份直接拒绝，绝不回落官方商户。
- 第三方前端仅拿「短期限、订单作用域」的支付会话（payment_pay_sessions）；
  持久商户密钥只存在于商户服务端，不出现在浏览器请求/打包产物。
纯逻辑：可注入 db/now，零副作用 import。
"""
import secrets
import sqlite3
from datetime import datetime, timedelta

from ..contracts import now_iso
from . import asset_for_channel

SESSION_TTL_MINUTES = 15   # 支付会话短期限（订单作用域）


def _merchant_row(db, merchant):
    return db.execute('SELECT code, name, enabled, tron_address, eth_address, bsc_address, sol_address'
                      ' FROM payment_merchants WHERE code=?', (merchant,)).fetchone()


def _merchant_address(row, chain):
    """商户自配收款地址；未配 → 返回空（拒绝，不回落平台资金池）。"""
    if not row:
        return ''
    return ((row['tron_address'] or '').strip() if chain == 'TRON' else
            (row['eth_address'] or '').strip() if chain == 'ETH' else
            (row['bsc_address'] or '').strip() if chain == 'BSC' else
            (row['sol_address'] or '').strip() if chain == 'SOL' else '')


def create_merchant_intent(db, merchant, external_order_no, external_user_ref,
                           amount_units, chain, idempotency_key=None, now=None, ttl_minutes=None):
    """外部商户创建支付意图（纯逻辑）：
    校验商户有效 → 校验该链已配收款地址（未配拒绝）→ 落订单（金额由商户按最小单位指定）
    → 签发短期限、订单作用域支付会话。
    返回 (order_row, session_token, None) 或 (None, None, err)。
    不依赖 Gold 用户/套餐：username=external_user_ref、plan_id=外部订单引用。"""
    now = now_iso() if now is None else now
    if not merchant or not external_order_no or not amount_units:
        return None, None, '缺少商户标识/外部订单号/金额（最小单位）'
    row = _merchant_row(db, merchant)
    if not row or not row['enabled']:
        return None, None, '无效商户身份，拒绝下单（不回退官方商户）'
    if chain not in ('TRON', 'ETH', 'BSC', 'SOL'):
        return None, None, '不支持的支付链 %s' % chain
    receive = _merchant_address(row, chain)
    if not receive:
        return None, None, '商户 %s 未配置 %s 链收款地址，通道未开通（不回落平台资金池）' % (merchant, chain)
    asset = asset_for_channel(chain)
    if not asset:
        return None, None, '不支持的支付链 %s（未登记资产/通道停用）' % chain

    # 幂等：外部订单号唯一（同单重复 → 返回同一订单与会话，不生成多笔）
    if idempotency_key or external_order_no:
        hit = db.execute("SELECT * FROM payment_orders WHERE idempotency_key=? ORDER BY id DESC LIMIT 1",
                         (idempotency_key or external_order_no,)).fetchone()
        if hit:
            tok = _issue_session(db, hit['order_no'], merchant, now, ttl_minutes)
            return hit, tok, None

    asset_key = '%s@%s:%s' % (asset['chain'], asset['network'],
                              asset.get('contract') or asset.get('mint'))
    order_no = 'MP%s%04d' % (datetime.strptime(now, '%Y-%m-%d %H:%M:%S').strftime('%Y%m%d%H%M%S'),
                             secrets.randbelow(10000))
    try:
        db.execute(
            "INSERT INTO payment_orders(order_no, username, plan_id, plan_name, price_usdt, discount,"
            " amount_usdt, tail, status, created_at, expired_at, channel, merchant, receive_address,"
            " idempotency_key, amount_units, asset_key, allocation_key)"
            " VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?)",
            (order_no, external_user_ref, external_order_no, '商户商品', 0, 1.0, 0, 0, now,
             (datetime.strptime(now, '%Y-%m-%d %H:%M:%S') + timedelta(minutes=ttl_minutes or 30)).strftime('%Y-%m-%d %H:%M:%S'),
             chain, merchant, receive, idempotency_key or external_order_no, amount_units, asset_key,
             '%s|%s' % (receive, asset_key)))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        hit = db.execute("SELECT * FROM payment_orders WHERE idempotency_key=? ORDER BY id DESC LIMIT 1",
                         (idempotency_key or external_order_no,)).fetchone()
        if hit:
            tok = _issue_session(db, hit['order_no'], merchant, now, ttl_minutes)
            return hit, tok, None
        return None, None, '并发冲突，请重试'
    order = db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,)).fetchone()
    tok = _issue_session(db, order_no, merchant, now, ttl_minutes)
    return order, tok, None


def _issue_session(db, order_no, merchant, now, ttl_minutes=None):
    """签发短期限、订单作用域支付会话（session_token 绑定单一订单，过期即失效）。"""
    token = 'ps_' + secrets.token_hex(16)
    expires = (datetime.strptime(now, '%Y-%m-%d %H:%M:%S')
               + timedelta(minutes=ttl_minutes or SESSION_TTL_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
    db.execute('INSERT INTO payment_pay_sessions(session_token, order_no, merchant, expires_at, created_at)'
               ' VALUES(?,?,?,?,?)', (token, order_no, merchant, expires, now))
    db.commit()
    return token


def resolve_pay_session(db, session_token, order_no, now=None):
    """第三方前端凭支付会话取订单：token 存在、绑定该订单、未过期才放行。
    浏览器仅持此会话；持久商户密钥不参与。返回 (order_row, None) 或 (None, err)。"""
    now = now_iso() if now is None else now
    if not session_token or not order_no:
        return None, '缺少支付会话或订单号'
    s = db.execute('SELECT order_no, merchant, expires_at FROM payment_pay_sessions WHERE session_token=?',
                   (session_token,)).fetchone()
    if not s:
        return None, '支付会话无效或不存在'
    if s['order_no'] != order_no:
        return None, '支付会话不属于该订单（订单作用域）'
    if s['expires_at'] < now:
        return None, '支付会话已过期，请重新下单'
    order = db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,)).fetchone()
    if not order:
        return None, '订单不存在'
    return order, None

