# -*- coding: utf-8 -*-
"""
payments/orders —— UGP-17 可幂等订单与回执受理（纯逻辑，可注入 db/now，零副作用 import）

- 订单由可信会话（principal）和商品标识创建；无效商户 key 必须拒绝，**绝不回退官方站**。
- 请求幂等 + 订单状态机：同一幂等键重复/并发/超时重试返回同一订单，不生成多笔待支付
  （DB 部分唯一索引 idx_pay_idem 兜底）。
- 金额分配：按资产+收款地址+有效窗口分配，活动订单金额唯一（idx_pay_alloc 数据库保证）；
  无槽位明确返回忙，不扩大随机尝试伪装成功。
- 回执仅提交线索：进入 submitted（待核验）；过期/少付/多付/晚到账/不明确归属 →
  可审计异常（exception 队列），不自动发权益。confirmed 仅由链上核验（UGP-03 保持）写入。
"""
import sqlite3
import time
import threading
from datetime import datetime, timedelta

from ..contracts import now_iso
from ..compat import LEGACY_PRODUCT_REF
from .money import parse_amount_units, format_amount_units
from .assets import resolve_asset
from . import (
    load_payment_channels, read_payment_config, lock_payment_intent,
    verify_payment_intent, plan_by_id)

# 订单状态机：活动（可匹配到账）→ 待核验 → 已确认；异常/过期为终态（不自动发权益）
ACTIVE_STATUS = ('pending', 'submitted', 'confirming')
EXCEPTION_STATUS = ('expired', 'cancelled', 'exception')

# 金额分配窗口：同一资产+收款地址内并发的活动订单，金额最小单位必须唯一
_ALLOC_LOCK = threading.Lock()


def _merchant_valid(db, merchant):
    """商户有效性：存在且启用。无效商户 key 一律拒绝（调用方不得回退官方站）。"""
    if not merchant:
        return True   # 平台自有下单（ultima-gold）不走商户凭据
    row = db.execute('SELECT code, enabled FROM payment_merchants WHERE code=?', (merchant,)).fetchone()
    return bool(row) and bool(row[1])


def _receive_for(db, merchant, channel):
    """收款地址（UGP-30 隔离）：
    - 外部商户（payment_merchants 有记录且非平台自有）：仅允许其自配地址，未配 → 返回空（拒绝，
      不悄悄回落平台资金池/官方商户）。
    - 平台自有下单（ultima-gold / 无 merchant）：走通道默认地址。
    """
    receive = ''
    _mrow = db.execute('SELECT tron_address, eth_address, bsc_address, sol_address FROM payment_merchants WHERE code=?',
                       (merchant,)).fetchone()
    if _mrow:
        _idx = {'TRON': 0, 'ETH': 1, 'BSC': 2, 'SOL': 3}.get(channel)
        if _idx is not None and (_mrow[_idx] or '').strip():
            return _mrow[_idx].strip()
        # 商户记录存在但未配该链 → 拒绝（不回落平台资金池）
        if merchant not in (None, '', 'ultima-gold'):
            return ''
    if not receive:
        receive = (load_payment_channels(db).get(channel) or {}).get('address') or ''
    return receive


def _activity_hit(db, asset_key, receive, amount_units):
    return db.execute(
        "SELECT order_no FROM payment_orders"
        " WHERE asset_key=? AND receive_address=? AND amount_units=?"
        " AND status IN ('pending','submitted','confirming')",
        (asset_key, receive, amount_units)).fetchone()


def _allocate_amount_units(db, plan, asset, receive, now=None):
    """按链/网络/收款地址/有效窗口分配最小单位金额：候选尾数（0.1~0.9 最小单位）逐一查
    活动占用；全占 → 返回 (None, '忙')，明确返回忙而非扩大随机伪装成功。
    进程锁 + DB 活动唯一索引双保险。"""
    decimals = asset['decimals']
    try:
        price_units = parse_amount_units(str(plan.get('priceUsdt')), decimals)
    except ValueError as e:
        return None, '套餐价无法用该链最小单位精确表示: %s' % e
    unit = 10 ** (decimals - 1)
    asset_key = '%s@%s:%s' % (asset['chain'], asset['network'],
                              asset.get('contract') or asset.get('mint'))
    chosen = None
    for tail in (unit * k for k in range(1, 10)):   # 0.1~0.9 最小单位尾数（窗口内候选）
        amount_units = price_units + tail
        if _activity_hit(db, asset_key, receive, amount_units) is None:
            chosen = amount_units
            break
    if chosen is None:
        return None, '该收款地址活动订单金额槽位已满，当前忙，请稍后重试'
    return (price_units, chosen, chosen - price_units, asset_key)


def create_payment_intent(db, principal, product_ref, chain, idempotency_key,
                          plan=None, plan_id=None, merchant=None, receive_address=None,
                          now=None, _asset_override=None):
    """C3 下单（UGP-17 幂等版）：可信会话 + 商品标识 + 幂等键创建订单。
    返回 (order_row, None) 或 (None, err)；同幂等键重复/并发 → 返回同一订单。"""
    now = now_iso() if now is None else now
    # 1) 可信会话校验：必须能确定下单主体（user 或有效商户）
    if not principal or not principal.get('role'):
        return None, '缺少可信会话，无法下单'
    if principal.get('role') == 'merchant':
        if not _merchant_valid(db, principal.get('merchant') or principal.get('username')):
            return None, '无效商户 key，拒绝下单（不回退官方商户）'
    elif principal.get('role') not in ('user', 'admin'):
        return None, '无下单权限'
    # 2) 商品解析：plan 或 plan_id（仅套餐权威库）
    if plan is None:
        plan = plan_by_id(db, plan_id)
    if not plan or plan.get('product') != LEGACY_PRODUCT_REF:
        return None, '套餐不存在或非 Ultima Gold 套餐'
    # 3~6) 幂等查 + 分配 + 落库 置于进程写锁内（多连接并发写串行，避免 SQLite locked；
    #       同幂等键并发由唯一索引兜底 → 重查幂等返回同一订单）
    with _ALLOC_LOCK:
        if idempotency_key:
            hit = db.execute("SELECT * FROM payment_orders WHERE idempotency_key=? ORDER BY id DESC LIMIT 1",
                             (idempotency_key,)).fetchone()
            if hit:
                return hit, None
        # 4) 商户归属：无效商户已拒绝；有效商户不能回退官方站
        merchant = (merchant or '').strip() or 'ultima-gold'
        receive = receive_address or _receive_for(db, merchant, chain)
        if not receive:
            return None, '%s 链收款地址未配置，通道暂未开通' % chain
        # 5) 金额分配（最小单位唯一；无槽位 → 忙）
        asset = _asset_override
        if asset is None:
            from . import asset_for_channel
            asset = asset_for_channel(chain)
        if not asset:
            return None, '不支持的支付链 %s（未登记资产/通道停用）' % chain
        got = _allocate_amount_units(db, plan, asset, receive, now)
        if got[0] is None:
            return None, got[1]
        price_units, amount_units, tail_units, asset_key = got
        # 6) 落库（含幂等键/最小单位/资产键）；并发冲突由唯一索引兜底 → 重查幂等或返回忙
        while True:
            order_no = 'PAY%s%04d' % (datetime.now().strftime('%Y%m%d%H%M%S'),
                                      int(time.time() * 1000) % 10000)
            if not db.execute('SELECT id FROM payment_orders WHERE order_no=?', (order_no,)).fetchone():
                break
        try:
            db.execute(
                "INSERT INTO payment_orders(order_no, username, plan_id, plan_name, price_usdt, discount,"
                " amount_usdt, tail, status, created_at, expired_at, channel, merchant, receive_address,"
                " idempotency_key, amount_units, asset_key, allocation_key)"
                " VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?)",
                (order_no, principal.get('username') or principal.get('merchant') or '',
                 plan.get('id'), plan.get('name'), float(format_amount_units(price_units, asset['decimals'])),
                 plan.get('discount'), float(format_amount_units(amount_units, asset['decimals'])),
                 float(format_amount_units(tail_units, asset['decimals'])), now,
                 _expire_at(db, now), chain, merchant, receive,
                 idempotency_key or None, amount_units, asset_key, '%s|%s' % (receive, asset_key)))
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            if idempotency_key:
                hit = db.execute("SELECT * FROM payment_orders WHERE idempotency_key=? ORDER BY id DESC LIMIT 1",
                                 (idempotency_key,)).fetchone()
                if hit:
                    return hit, None
            return None, '金额分配并发冲突，请重试'
    return db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,)).fetchone(), None


def _row_map(cursor):
    cols = [d[0] for d in cursor.description]
    return lambda r: dict(zip(cols, r))


def _expire_at(db, now=None):
    cfg = read_payment_config(db)
    ttl = int(cfg.get('ttlMinutes') or 30)
    return (datetime.strptime(now, '%Y-%m-%d %H:%M:%S') + timedelta(minutes=ttl)).strftime('%Y-%m-%d %H:%M:%S')


def _audit_exception(db, order_no, reason, tx_hash='', now=None):
    """可审计异常登记：过期/少付/多付/晚到账/不明确归属落 exception 队列，不自动发权益。"""
    now = now_iso() if now is None else now
    db.execute("INSERT INTO payment_order_exceptions(order_no, reason, tx_hash, created_at)"
               " VALUES(?,?,?,?)", (order_no, reason, tx_hash or '', now))
    db.execute("UPDATE payment_orders SET status='exception' WHERE order_no=?", (order_no,))
    db.commit()


def submit_receipt(db, order_no, tx_hash, amount_units=None, now=None):
    """回执受理（仅提交线索）：pending → submitted（待核验队列）。
    过期/少付/多付/晚到账/不明确归属 → exception（可审计），不自动发权益。"""
    now = now_iso() if now is None else now
    cur = db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,))
    row = cur.fetchone()
    if not row:
        return None, '订单不存在'
    m = _row_map(cur)(row)
    status = m['status']
    if status in ('confirmed', 'submitted', 'confirming'):
        return None, '订单已进入核验/确认，回执重复提交无效'
    if status in EXCEPTION_STATUS:
        _audit_exception(db, order_no, '订单已处于终态 %s，晚到账/重复回执' % status, tx_hash, now)
        return None, '订单已失效/异常，晚到账不自动发权益'
    # 金额核对（最小单位）：少付/多付 → 可审计异常
    expect_units = m.get('amount_units')
    if expect_units is not None and amount_units is not None and amount_units != expect_units:
        _audit_exception(db, order_no, '金额不匹配：订单 %d，到账 %d' % (expect_units, amount_units), tx_hash, now)
        return None, '金额不匹配，进入可审计异常'
    if amount_units is None:
        _audit_exception(db, order_no, '回执缺少金额，归属不明确', tx_hash, now)
        return None, '缺少金额，归属不明确'
    # 通过：仅提交线索（不自动 confirmed / 不发权益；链上核验另行流转）
    db.execute("UPDATE payment_orders SET status='submitted', tx_hash=?, paid_at=?, source='test'"
               " WHERE order_no=?", (tx_hash or '', now, order_no))
    db.commit()
    return db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,)).fetchone(), None


def expire_stale_orders(db, now=None):
    """活动订单超时未到账 → expired（金额槽位释放，可被新订单复用）。"""
    now = now_iso() if now is None else now
    cur = db.execute("UPDATE payment_orders SET status='expired'"
                     " WHERE status='pending' AND expired_at < ?", (now,))
    db.commit()
    return cur.rowcount

