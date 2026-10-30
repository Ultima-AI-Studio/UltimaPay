# -*- coding: utf-8 -*-
"""
payments —— 支付域（UGP-06 从 feed_server 抽出，逻辑单份权威）

覆盖：通道配置（静态元信息 + DB 可配置）、订单（创建/查询/回执/回调/超时失效）、
链上查账（TRON/EVM/SOL，RPC 池遍历）。UGP-03 止血规则保持：回执路径只写 submitted，
永不自动 confirmed/settled（onchain 核验流程未实现前）。

DB 通过 `db` 参数注入；外部 Provider（链上 RPC、出站回调）调用发生在函数执行时。
模块 import 无副作用。
"""
import hmac
import json
import os
import random
import secrets
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from ..contracts import now_iso
from ..catalog import read_subscription_plans
from ..compat import LEGACY_PRODUCT_REF
from .money import parse_amount_units, format_amount_units  # UGP-16 整数最小单位
from .assets import resolve_asset, decimals_for  # UGP-16 资产注册表（chain+network+contract）

# ── 通道默认值 / RPC 端点（与 feed_server 原值一致）──
PAYMENT_ADDRESS_DEFAULT = 'TFa8L4TXqVPzdEQ5LgNCeEGDVJAbX22dA6'  # TRON TRC-20 收款地址（用户确认）
PAYMENT_TTL_DEFAULT = 30       # 下单后 30 分钟未到账自动失效
PAYMENT_POLL_SECONDS = 30      # 主动查链轮询间隔
TRONGRID_URL = 'https://api.trongrid.io/v1/accounts/%s/transactions/trc20'
SOLANA_RPC_DEFAULT = 'https://api.mainnet-beta.solana.com'  # 公共 RPC 可配置（正式运营建议境外/付费端点）
USDT_SPL_MINT = 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'  # Solana 主网 USDT SPL-token
EVM_USDT = {
    'ETH': ('0xdAC17F958D2ee523a2206206994597C13D831ec7', 6),   # USDT ERC-20 / 6 位
    'BSC': ('0x55d398326f99059fF775485246999027B3197955', 18),  # USDT BEP-20 / 18 位
}
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

# ── 支付链适配器登记（Ultima Pay 多链聚合）──
# address 为空表示该链收款地址未配置（通道未开通，下单会明确拒绝）
# rpcUrl 为主查询端点，rpcBackup 为备选池（逗号分隔，主端点连不上时自动切换尝试）
_PAYMENT_CHANNELS = {
    'TRON': {'name': 'TRON', 'network': 'TRON TRC-20', 'currency': 'USDT',
             'address': PAYMENT_ADDRESS_DEFAULT, 'enabled': True,
             'rpcUrl': 'https://api.trongrid.io', 'rpcBackup': ''},
    'ETH': {'name': 'Ethereum', 'network': 'Ethereum ERC-20', 'currency': 'USDT',
            'address': '0x421565A03A43857BdcAB534FB8C8fD1C3158E06a', 'enabled': True,
            'rpcUrl': 'https://ethereum.publicnode.com',
            'rpcBackup': 'https://ethereum-rpc.publicnode.com,https://rpc.ankr.com/eth,https://eth.llamarpc.com'},
    'BSC': {'name': 'BSC', 'network': 'BSC BEP-20', 'currency': 'USDT',
            'address': '0x421565A03A43857BdcAB534FB8C8fD1C3158E06a', 'enabled': True,
            'rpcUrl': 'https://bsc-dataseed.bnbchain.org',
            'rpcBackup': 'https://bsc-dataseed.binance.org,https://rpc.ankr.com/bsc'},
    'SOL': {'name': 'Solana', 'network': 'Solana SPL · USDT', 'currency': 'USDT',
            'address': '', 'enabled': False,  # 地址待配置；DB payment_channel_config 覆盖
            'rpcUrl': 'https://solana-rpc.publicnode.com',
            'rpcBackup': 'https://api.mainnet-beta.solana.com,https://rpc.ankr.com/solana'},
}


def _log(msg):
    print('[app.payments] %s' % msg, file=sys.stderr, flush=True)


def _rpc_list(db, ch):
    """该链查询端点池：[主端点] + 备选池（去空、去重）。"""
    c = _PAYMENT_CHANNELS.get(ch) or {}
    main = (c.get('rpcUrl') or '').strip()
    backup = [u.strip() for u in (c.get('rpcBackup') or '').split(',') if u.strip()]
    out = []
    for u in [main] + backup:
        if u and u not in out:
            out.append(u)
    return out


def load_payment_channels(db):
    """合并多链配置：静态元信息（名称/网络/币种/默认 RPC 池）+ 数据库可配置项（地址/RPC/启停）。
    DB 有记录则覆盖默认（地址留空=该链未开通；RPC 留空=用默认端点），无记录时回落静态默认。"""
    chs = json.loads(json.dumps(_PAYMENT_CHANNELS))
    try:
        rows = db.execute('SELECT channel, address, enabled, rpc_url, rpc_backup FROM payment_channel_config').fetchall()
    except Exception:
        rows = []
    for ch, address, enabled, rpc_url, rpc_backup in rows:
        if ch in chs:
            chs[ch]['address'] = address or ''
            chs[ch]['enabled'] = bool(enabled)
            if rpc_url:
                chs[ch]['rpcUrl'] = rpc_url
            if rpc_backup is not None:
                chs[ch]['rpcBackup'] = rpc_backup or ''
    return chs


def read_payment_config(db):
    """支付通道配置：地址 / 启停 / 超时分钟 / 官网回调密钥。"""
    row = db.execute('SELECT enabled, address, ttl_minutes, callback_secret FROM payment_config WHERE id=1').fetchone()
    if not row:
        return {'enabled': True, 'address': PAYMENT_ADDRESS_DEFAULT, 'ttlMinutes': PAYMENT_TTL_DEFAULT,
                'callbackSecret': ''}
    return {'enabled': bool(row[0]), 'address': row[1] or PAYMENT_ADDRESS_DEFAULT,
            'ttlMinutes': row[2] or PAYMENT_TTL_DEFAULT, 'callbackSecret': row[3] or ''}


def save_payment_config(db, enabled, address, ttl_minutes, callback_secret=''):
    """保存通道配置（后台通道卡修改入口）。"""
    address = (address or '').strip() or PAYMENT_ADDRESS_DEFAULT
    ttl = max(1, min(1440, int(ttl_minutes or PAYMENT_TTL_DEFAULT)))
    db.execute('INSERT INTO payment_config(id, enabled, address, ttl_minutes, callback_secret, updated_at)'
               " VALUES(1, ?, ?, ?, ?, ?)"
               " ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, address=excluded.address,"
               " ttl_minutes=excluded.ttl_minutes, callback_secret=excluded.callback_secret,"
               " updated_at=excluded.updated_at",
               (1 if enabled else 0, address, ttl, callback_secret or '', now_iso()))
    db.commit()
    return {'enabled': bool(enabled), 'address': address, 'ttlMinutes': ttl, 'callbackSecret': callback_secret or ''}


def payment_expire_at(db):
    return (datetime.now() + timedelta(minutes=read_payment_config(db)['ttlMinutes'])).strftime('%Y-%m-%d %H:%M:%S')


def plan_by_id(db, plan_id):
    for p in read_subscription_plans(db):
        if p['id'] == plan_id:
            return p
    return None


# ── 链 → 资产注册表键（chain@network:contract/mint，与 assets 注册表一致）──
_CHANNEL_ASSET = {
    'TRON': ('tron', 'mainnet', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'),
    'ETH': ('eth', 'mainnet', '0xdAC17F958D2ee523a2206206994597C13D831ec7'),
    'BSC': ('bsc', 'mainnet', '0x55d398326f99059fF775485246999027B3197955'),
    'SOL': ('sol', 'mainnet', 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'),
}


def asset_for_channel(channel):
    """通道 → 资产注册表项（chain+network+contract 定位；未登记/停用 → None）。"""
    spec = _CHANNEL_ASSET.get(channel)
    if not spec:
        return None
    a = resolve_asset(*spec)
    if not a or not a.get('enabled', True):
        return None
    return a


def lock_payment_intent(db, plan, channel='TRON', receive_address=None, product_ref=LEGACY_PRODUCT_REF,
                        merchant='ultima-gold', test_asset=None):
    """C3 PaymentIntent：服务端锁定商品价格快照（整数最小单位）、折扣是否已包含、
    期限/赠期与收款地址。**前端不能自行算价格**：显示价/币种精度/网络/收款地址等
    客户端参数一律不采纳，锁定完全由服务端套餐权威库 + 资产注册表决定。

    返回 (intent, None) 或 (None, 原因)。
    intent = {product_ref, plan_id, plan_name, asset{chain,network,contract/mint,decimals,symbol},
              price_units, amount_units, amount_decimal, discount_included, tail_units,
              duration_days, bonus_days, receive_address, channel, locked_at_utc}
    """
    cfg = read_payment_config(db)
    if not cfg['enabled']:
        return None, '支付通道已停用，暂不能下单'
    asset = test_asset or asset_for_channel(channel)
    if asset is None:
        return None, '不支持的支付链 %s（未登记资产/通道停用）' % channel
    if plan is None or plan.get('product') != LEGACY_PRODUCT_REF:
        return None, '套餐不存在或非 Ultima Gold 套餐'
    if not plan.get('enabled'):
        return None, '该套餐已停用，暂不能购买'
    price = plan.get('priceUsdt')
    if price is None or price <= 0:
        return None, '该套餐无需支付（体验卡 0 价）'
    decimals = asset['decimals']
    try:
        price_units = parse_amount_units(str(price), decimals)
    except ValueError as e:
        return None, '套餐价无法用该链最小单位精确表示: %s' % e
    # 尾数避让（现有防撞单机制，UGP-17 改为按链/地址/窗口分配）：同样以最小单位加
    tail_units = 0
    for _ in range(20):
        tail_units = round(random.randint(1, 9) * (10 ** (decimals - 1)))  # 0.1~0.9 最小单位尾数
        amount_units = price_units + tail_units
        hit = db.execute("SELECT order_no FROM payment_orders WHERE amount_usdt=? AND status IN ('pending','paid')",
                         (float(format_amount_units(amount_units, decimals)),)).fetchone()
        if not hit:
            break
    else:
        return None, '尾数避让失败，请重试'
    receive = (receive_address or '').strip()
    if not receive:
        supported = load_payment_channels(db)
        receive = (supported.get(channel) or {}).get('address') or ''
    if not receive:
        return None, '%s 链收款地址未配置，通道暂未开通' % (asset.get('symbol') or channel)
    return {
        'product_ref': product_ref,
        'plan_id': plan.get('id'),
        'plan_name': plan.get('name'),
        'asset': {'chain': asset['chain'], 'network': asset['network'],
                  'contract': asset.get('contract') or asset.get('mint'),
                  'decimals': asset['decimals'], 'symbol': asset.get('symbol', 'USDT')},
        'price_units': price_units,
        'amount_units': amount_units,
        'amount_decimal': format_amount_units(amount_units, decimals),  # 十进制字符串，前端只展示不计算
        'discount_included': bool(plan.get('discount', 0)),
        'tail_units': tail_units,
        'duration_days': plan.get('durationDays') or plan.get('duration_days') or 0,
        'bonus_days': plan.get('bonusDays') or plan.get('bonus_days') or 0,
        'receive_address': receive,
        'channel': channel,
        'merchant': merchant or 'ultima-gold',
        'locked_at_utc': now_iso(),
    }, None


def verify_payment_intent(intent, plan, asset=None, amount_units=None, receive_address=None):
    """校验已锁定订单未被篡改：金额最小单位必须等于 套餐价+尾数 在资产精度下的精确值；
    显示价/币种精度/网络/收款地址任何偏离权威源 → 拒绝。返回 (ok, reason)。"""
    if not intent or not plan:
        return False, '缺少订单或套餐快照'
    asset = asset or intent.get('asset') or {}
    decimals = asset.get('decimals')
    if decimals is None:
        return False, '缺少资产精度'
    try:
        expect_price = parse_amount_units(str(plan.get('priceUsdt')), decimals)
    except ValueError as e:
        return False, '套餐价精度异常: %s' % e
    expect_amount = expect_price + int(intent.get('tail_units') or 0)
    if int(intent.get('amount_units') or 0) != expect_amount:
        return False, '金额被篡改：锁定 %d ≠ 权威 %d' % (intent.get('amount_units'), expect_amount)
    if int(intent.get('price_units') or 0) != expect_price:
        return False, '价格快照被篡改'
    # 显示面（十进制字符串）必须与最小单位一致：篡改显示价/精度 → 拒绝
    if format_amount_units(int(intent.get('amount_units') or 0), decimals) != (intent.get('amount_decimal') or ''):
        return False, '显示金额被篡改：与最小单位不一致'
    if receive_address is not None and (intent.get('receive_address') or '') != (receive_address or ''):
        return False, '收款地址被篡改'
    return True, 'OK'


def create_payment_order(db, username, plan_id, channel='TRON', merchant='ultima-gold'):
    """发起支付订单（Ultima Pay 自助下单）：金额 = 套餐价 + 随机一位小数尾数（查重避让，保证每单金额唯一）。
    channel 为支付链标识（多链聚合预留）；merchant 为订单归属接入方。
    UGP-16：金额先经 lock_payment_intent 整数最小单位锁定（服务端定价快照），前端不能自行算价格。"""
    cfg = read_payment_config(db)
    if not cfg['enabled']:
        return None, '支付通道已停用，暂不能下单'
    plan = plan_by_id(db, plan_id)
    if not plan or plan.get('product') != LEGACY_PRODUCT_REF:
        return None, '套餐不存在或非 Ultima Gold 套餐'
    if not plan.get('enabled'):
        return None, '该套餐已停用，暂不能购买'
    if plan.get('priceUsdt') is None or plan['priceUsdt'] <= 0:
        return None, '该套餐无需支付（体验卡 0 价）'
    user = db.execute('SELECT username FROM users WHERE username=?', (username,)).fetchone()
    if not user:
        return None, '用户不存在'
    # 收款地址双轨：接入方自配优先（tron/eth/bsc/sol_address），未配回退平台通道默认地址
    receive = ''
    _mrow = db.execute('SELECT tron_address, eth_address, bsc_address, sol_address FROM payment_merchants WHERE code=?',
                       (merchant,)).fetchone()
    if _mrow:
        _idx = {'TRON': 0, 'ETH': 1, 'BSC': 2, 'SOL': 3}.get(channel)
        if _idx is not None and (_mrow[_idx] or '').strip():
            receive = _mrow[_idx].strip()
    intent, err = lock_payment_intent(db, plan, channel=channel, receive_address=receive,
                                      product_ref=LEGACY_PRODUCT_REF, merchant=merchant)
    if err:
        return None, err
    ok, verr = verify_payment_intent(intent, plan, receive_address=intent['receive_address'])
    if not ok:
        return None, '订单锁定校验失败: %s' % verr
    decimals = intent['asset']['decimals']
    price_decimal = format_amount_units(intent['price_units'], decimals)
    amount_decimal = format_amount_units(intent['amount_units'], decimals)
    tail_decimal = format_amount_units(intent['tail_units'], decimals)
    order_no = 'PAY%s%04d' % (datetime.now().strftime('%Y%m%d%H%M%S'), random.randint(0, 9999))
    while db.execute('SELECT id FROM payment_orders WHERE order_no=?', (order_no,)).fetchone():
        order_no = 'PAY%s%04d' % (datetime.now().strftime('%Y%m%d%H%M%S'), random.randint(0, 9999))
    now = now_iso()
    db.execute('INSERT INTO payment_orders(order_no, username, plan_id, plan_name, price_usdt, discount,'
               ' amount_usdt, tail, status, created_at, expired_at, channel, merchant, receive_address)'
               ' VALUES(?,?,?,?,?,?,?,?,\'pending\',?,?,?,?,?)',
               (order_no, username, plan_id, plan['name'], float(price_decimal), plan['discount'],
                float(amount_decimal), float(tail_decimal), now, payment_expire_at(db),
                channel, merchant or 'ultima-gold', intent['receive_address']))
    db.commit()
    row = db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,)).fetchone()
    return row, None


def payment_row(r):
    return {'id': r[0], 'orderNo': r[1], 'username': r[2], 'planId': r[3], 'planName': r[4],
            'priceUsdt': r[5], 'discount': r[6], 'amountUsdt': r[7], 'tail': r[8],
            'status': r[9], 'txHash': r[10], 'createdAt': r[11], 'paidAt': r[12], 'expiredAt': r[13],
            'channel': r[14] if len(r) > 14 else 'TRON',
            'merchant': r[15] if len(r) > 15 else 'ultima-gold',
            'receiveAddress': r[16] if len(r) > 16 else '',
            'callbackStatus': r[17] if len(r) > 17 else '',
            'callbackAt': r[18] if len(r) > 18 else ''}


def read_payment_orders(db, status=None):
    if status and status != 'all':
        rows = db.execute('SELECT * FROM payment_orders WHERE status=? ORDER BY id DESC', (status,)).fetchall()
    else:
        rows = db.execute('SELECT * FROM payment_orders ORDER BY id DESC').fetchall()
    return [payment_row(r) for r in rows]


def read_merchant_orders(db, merchant, status=None):
    """按接入方查收款侧支付流水 + 汇总统计（接入方管理「查看」抽屉数据源）。"""
    if status and status != 'all':
        rows = db.execute('SELECT * FROM payment_orders WHERE merchant=? AND status=? ORDER BY id DESC',
                          (merchant, status)).fetchall()
    else:
        rows = db.execute('SELECT * FROM payment_orders WHERE merchant=? ORDER BY id DESC', (merchant,)).fetchall()
    stats = db.execute(
        "SELECT COUNT(*),"
        " SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END),"
        " COALESCE(SUM(CASE WHEN status='confirmed' THEN amount_usdt ELSE 0 END), 0)"
        " FROM payment_orders WHERE merchant=?", (merchant,)).fetchone()
    return [payment_row(r) for r in rows], {
        'total': stats[0] or 0,
        'pending': stats[1] or 0,
        'confirmed': stats[2] or 0,
        'expired': stats[3] or 0,
        'confirmedUsdt': round(stats[4] or 0, 2),
    }


def read_payment_order(db, order_no):
    """单笔订单详情（Ultima Pay 订单状态查询）。"""
    row = db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,)).fetchone()
    return payment_row(row) if row else None


def conn_row(db, order_no):
    r = db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,)).fetchone()
    return r


def confirm_payment_order(db, order_no, tx_hash, amount_usdt=None, source='receipt'):
    """Deprecated receipt intake: enter submitted, never confirm payment.

    UGP-03 止血规则：
    - confirmed 仅由 verify_receipt → record_verified_payment 链路写入；
      本函数不属于该可信链路，source='onchain' 也不会绕过核验。
    - 客户端回执 / 手动核销 / 外部回调 / 轮询记录（receipt/manual/callback/poll）
      一律只标记 submitted，不开通权益、不发确认回调，返回「等待核验」。
    - 空哈希、任意哈希提交均不会增加权益或写 confirmed。
    """
    row = db.execute('SELECT * FROM payment_orders WHERE order_no=?', (order_no,)).fetchone()
    if not row:
        return None, '订单不存在'
    if row[9] != 'pending':
        return None, '订单状态为 %s，不可确认' % row[9]
    if amount_usdt is not None and abs(amount_usdt - row[7]) > 0.005:
        return None, '金额不匹配：订单 %.2f，到账 %.2f' % (row[7], amount_usdt)
    if source == 'onchain':
        # 链上核验流程占位：未实现 ADR 深度/连续确认门前不放开自动确认
        return None, '链上核验流程尚未启用，无法确认到账'
    # 回执路径：只记录 submitted（等待核验），绝不直接 confirmed / 开通权益 / 发回调；
    # 来源标签：回执/手动/回调/轮询路径一律 test（未核验占位），链上核验（UGP-16 后）才可 live
    now = now_iso()
    db.execute("UPDATE payment_orders SET status='submitted', tx_hash=?, paid_at=?, source='test' WHERE order_no=?",
               (tx_hash or '', now, order_no))
    db.commit()
    return payment_row(conn_row(db, order_no)), None


def expire_stale_orders(db):
    """超时（默认 30 分钟）未到账 → 失效。"""
    now = now_iso()
    cur = db.execute("UPDATE payment_orders SET status='expired' WHERE status='pending' AND expired_at < ?", (now,))
    db.commit()
    return cur.rowcount


def rotate_merchant_secret(db, code):
    """受控一次性轮换商户接入凭据（secret）：旧值立即失效；新值仅本次返回（一次性领用）。

    持久密钥不编进浏览器 SDK/公开邮件附件；轮换后 SDK 需重新下发。
    """
    new_secret = secrets.token_urlsafe(24)
    db.execute("UPDATE payment_merchants SET secret=? WHERE code=?", (new_secret, code))
    db.commit()
    return new_secret


def merchant_secret_ok(db, code, secret):
    """校验商户 secret 是否当前有效（轮换后旧值 False、新值 True）。"""
    row = db.execute('SELECT secret FROM payment_merchants WHERE code=?', (code,)).fetchone()
    return bool(row) and bool(row[0]) and hmac.compare_digest(row[0], secret or '')


def fetch_trc20_incoming(db, address):
    """主动查链：拉取收款地址最近 USDT（TRC-20）原始事件。

    保留最小单位、合约和交易字段供 verification.parse_trc20_event 严格核验；
    仅补充当前深度并把 TronGrid 毫秒时间戳归一为 Unix 秒。
    遍历 TRON RPC 池（主端点连不上自动切换备选），全部失败返回空并记日志。"""
    pool = _rpc_list(db, 'TRON') or [TRONGRID_URL]
    last_err = None
    for rpc in pool:
        try:
            base = rpc.rstrip('/')
            if '/v1/accounts/' in base:
                url = TRONGRID_URL % address
            else:
                url = base + '/v1/accounts/%s/transactions/trc20' % address
            url += '?only_confirmed=true&only_to=true&limit=200'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            origin = base.split('/v1/accounts/', 1)[0]
            head_req = urllib.request.Request(
                origin + '/wallet/getnowblock', data=b'{}',
                headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
            head = json.loads(urllib.request.urlopen(head_req, timeout=10).read().decode())
            latest = int((((head.get('block_header') or {}).get('raw_data') or {}).get('number')))
            out = []
            for t in data.get('data', []) or []:
                sym = (t.get('token_info') or {}).get('symbol', '')
                if sym != 'USDT':
                    continue
                try:
                    int(t.get('value'))
                except (TypeError, ValueError):
                    continue
                event = dict(t)
                block = event.get('block_number')
                if block is None:
                    tx_req = urllib.request.Request(
                        origin + '/wallet/gettransactioninfobyid',
                        data=json.dumps({'value': event.get('transaction_id')}).encode('utf-8'),
                        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
                    tx_info = json.loads(urllib.request.urlopen(tx_req, timeout=10).read().decode())
                    receipt_result = ((tx_info.get('receipt') or {}).get('result') or 'SUCCESS').upper()
                    if receipt_result != 'SUCCESS':
                        continue
                    block = tx_info.get('blockNumber')
                try:
                    block = int(block)
                except (TypeError, ValueError):
                    continue
                event['block_number'] = block
                timestamp = event.get('block_timestamp')
                if timestamp is not None:
                    timestamp = int(timestamp)
                    if timestamp > 10_000_000_000:
                        timestamp //= 1000
                    event['block_timestamp'] = timestamp
                event['depth'] = max(0, latest - block + 1)
                event['_observation_block'] = latest
                out.append(event)
            return out
        except Exception as e:
            last_err = '%s' % e
            _log('TRON 查链失败(%s): %s' % (rpc, e))
    _log('TRON RPC 池全部失败: %s' % last_err)
    return []


def evm_rpc_call(db, chain, method, params):
    """EVM JSON-RPC 调用：遍历该链 RPC 池，任一成功即返回结果；全部失败返回 None。"""
    pool = _rpc_list(db, chain)
    if not pool:
        return None
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    last_err = None
    for url in pool:
        try:
            req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
            resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            if resp.get('error'):
                last_err = str(resp.get('error'))
                continue
            return resp.get('result')
        except Exception as e:
            last_err = '%s' % e
            _log('%s RPC 失败(%s): %s' % (chain, url, e))
    if last_err:
        _log('%s RPC 池全部失败: %s' % (chain, last_err))
    return None


def fetch_evm_incoming(db, address, chain='ETH'):
    """主动查链：EVM 链（ETH/BSC）USDT 入账查询。
    eth_getLogs 过滤 Transfer 事件（topic2=收款地址），data 中金额按链精度换算：
    ETH USDT=6 位、BSC USDT=18 位。区块范围取最近 3000 块。"""
    spec = EVM_USDT.get(chain)
    if not spec:
        return []
    contract, decimals = spec
    latest = evm_rpc_call(db, chain, 'eth_blockNumber', [])
    if not latest:
        return []
    try:
        latest_int = int(latest, 16)
    except Exception:
        return []
    from_block = max(latest_int - 3000, 0)
    to_topic = '0x' + '0' * 24 + address[2:].lower()
    logs = evm_rpc_call(db, chain, 'eth_getLogs', [{
        'address': contract,
        'fromBlock': hex(from_block),
        'toBlock': 'latest',
        'topics': [TRANSFER_TOPIC, None, to_topic],
    }])
    if not logs:
        return []
    out = []
    block_times = {}
    for lg in logs:
        try:
            block_number = int(lg.get('blockNumber'), 16)
            int(lg.get('data') or '0x0', 16)
        except (TypeError, ValueError):
            continue
        event = dict(lg)
        event['depth'] = max(0, latest_int - block_number + 1)
        if block_number not in block_times:
            block_data = evm_rpc_call(db, chain, 'eth_getBlockByNumber',
                                      [hex(block_number), False]) or {}
            timestamp = block_data.get('timestamp')
            try:
                block_times[block_number] = int(timestamp, 16) if timestamp else None
            except (TypeError, ValueError):
                block_times[block_number] = None
        if block_times[block_number] is None:
            continue
        event['block_time'] = block_times[block_number]
        event['confirmed'] = not bool(event.get('removed'))
        event['_observation_block'] = latest_int
        out.append(event)
    return out


def solana_rpc(db, method, params):
    """Solana JSON-RPC 调用：遍历 SOL RPC 池（主端点连不上自动切换备选），全部失败返回 None。"""
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    pool = _rpc_list(db, 'SOL') or [os.environ.get('SOLANA_RPC') or SOLANA_RPC_DEFAULT]
    last_err = None
    for url in pool:
        try:
            req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
            resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            if resp.get('error'):
                last_err = str(resp.get('error'))
                continue
            return resp.get('result')
        except Exception as e:
            last_err = '%s' % e
            _log('Solana RPC 失败(%s): %s' % (url, e))
    if last_err:
        _log('Solana RPC 池全部失败: %s' % last_err)
    return None


def fetch_solana_incoming(db, address):
    """主动查链：返回可由 parse_solana_tx 严格核验的原始交易。"""
    sigs = solana_rpc(db, 'getSignaturesForAddress', [address, {'limit': 30}])
    if not sigs:
        return []
    observation_slot = solana_rpc(db, 'getSlot', [{'commitment': 'finalized'}])
    if observation_slot is None:
        return []
    out = []
    for s in sigs:
        if not s.get('err') is None:  # 失败交易跳过
            continue
        tx = solana_rpc(db, 'getTransaction', [s['signature'], {'encoding': 'jsonParsed', 'maxSupportedTransactionVersion': 0}])
        if not tx:
            continue
        raw = json.loads(json.dumps(tx))
        raw['signature'] = s['signature']
        raw['confirmationStatus'] = s.get('confirmationStatus') or 'confirmed'
        raw['_observation_block'] = int(observation_slot)
        parsed_groups = [((raw.get('transaction') or {}).get('message') or {}).get('instructions') or []]
        parsed_groups.extend(
            group.get('instructions') or []
            for group in (raw.get('meta') or {}).get('innerInstructions') or []
        )
        trustworthy = True
        found_transfer = False
        for instructions in parsed_groups:
            for instruction in instructions:
                parsed = instruction.get('parsed') or {}
                if parsed.get('type') != 'transferChecked':
                    continue
                found_transfer = True
                info = parsed.get('info') or {}
                token_amount = info.get('tokenAmount') or {}
                if info.get('amount') is None and token_amount.get('amount') is not None:
                    info['amount'] = token_amount['amount']
                if not info.get('ataFor'):
                    account = solana_rpc(
                        db, 'getAccountInfo',
                        [info.get('destination'), {'encoding': 'jsonParsed', 'commitment': 'finalized'}]
                    ) or {}
                    account_info = (((account.get('value') or {}).get('data') or {}).get('parsed') or {}).get('info') or {}
                    if (not account_info.get('owner')
                            or account_info.get('mint') != info.get('mint')):
                        trustworthy = False
                        break
                    info['ataFor'] = account_info['owner']
            if not trustworthy:
                break
        if not trustworthy or not found_transfer:
            continue
        out.append(raw)
    return out

