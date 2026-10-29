# -*- coding: utf-8 -*-
"""
payments.reconciliation —— 只读对账与异常分类（UGP-29）

- 输入：链上到账证据（payment_ledger：order_no/tx_hash/chain/network/asset_key/amount_units）、
  支付流水（payment_orders）、权益履约（fulfillment_log）、可靠事件（payment_events）。
- 最小单位精确对账：amount_units（整数最小单位）逐订单与证据合计比对，diff=0 才 matched。
- 异常分类（每个异常 fixture 落入预期分类，不隐藏）：
  stale（订单过期/取消）、legacy（历史未核验记录，不计入真实收入与通过率）、
  unverified（无链证据）、underpaid / overpaid（少付/多付）、
  pending_fulfillment（已核验已到账但权益未履约）、callback_failed（回执回调失败）、unknown（保持待处理）。
- 只读零副作用：本模块不执行任何写操作（INSERT/UPDATE/DELETE）；测试以 query_only 连接运行验证。
- 后台修正操作需授权、理由与前后值（修正类写操作不属于本模块；对外退款/补偿需另列资金授权）。
"""
import json
from datetime import datetime

LEGACY_SOURCES = ('legacy', 'legacy_unverified', 'test')
# 允许链/网络/币种作为对账维度标签（USDT 最小单位由 amount_units 整数承载，不做隐式换算）


def _now(now_fn):
    return now_fn() if now_fn else datetime.now()


def _evidence_map(db):
    """按订单聚合链上到账证据（最小单位）。"""
    rows = db.execute('SELECT order_no, tx_hash, chain, network, asset_key, amount_units, block,'
                      ' block_time, evidence_id, event_id, created_at FROM payment_ledger').fetchall()
    out = {}
    for r in rows:
        o = out.setdefault(r['order_no'], [])
        o.append({'tx_hash': r['tx_hash'], 'chain': r['chain'], 'network': r['network'],
                  'asset_key': r['asset_key'], 'amount_units': r['amount_units'],
                  'block': r['block'], 'block_time': r['block_time'],
                  'evidence_id': r['evidence_id'], 'event_id': r['event_id'],
                  'created_at': r['created_at']})
    return out


def _fulfilled_orders(db):
    return {r['order_no'] for r in db.execute(
        "SELECT order_no FROM fulfillment_log WHERE status='applied'")}


def _callback_failed(db):
    return {r['order_no'] for r in db.execute(
        "SELECT order_no FROM payment_orders WHERE status='confirmed' AND callback_status='failed'")}


def _fmt_amount_units(units, asset_key):
    return '%d (%s)' % (units or 0, asset_key or '?')


def run_reconciliation(db, now_fn=None):
    """只读对账。返回报告 dict（不写库）。"""
    now = _now(now_fn)
    evidence = _evidence_map(db)
    fulfilled = _fulfilled_orders(db)
    callback_failed = _callback_failed(db)

    orders = db.execute('SELECT order_no, username, plan_id, amount_units, amount_usdt, status,'
                        ' source, tx_hash, callback_status, idempotency_key, created_at'
                        ' FROM payment_orders ORDER BY id').fetchall()

    categories = {}
    live_income = {}
    exceptions = []
    matched_live = 0
    abnormal_live = 0

    def bump(cls):
        categories[cls] = categories.get(cls, 0) + 1

    for o in orders:
        order_no = o['order_no']
        evs = evidence.get(order_no, [])
        is_legacy = (o['source'] or '') in LEGACY_SOURCES or o['amount_units'] is None

        # 1) 历史未核验记录：legacy（不计入真实收入/通过率；不把历史模拟核销当真实到账）
        if is_legacy:
            bump('legacy')
            exceptions.append({'order_no': order_no, 'cls': 'legacy', 'reason': 'legacy-unverified',
                               'detail': 'source=%s state=%s tx=%s evidence=%d' % (
                                   o['source'], o['status'], o['tx_hash'] or '', len(evs))})
            continue

        # 2) 过期/取消订单：stale（不参与收入）
        if o['status'] == 'expired':
            bump('stale')
            exceptions.append({'order_no': order_no, 'cls': 'stale', 'reason': 'order-expired',
                               'detail': 'amount_units=%s' % _fmt_amount_units(o['amount_units'], 'USDT')})
            continue

        # 3) 无链证据 → 未核验
        if not evs:
            bump('unverified')
            abnormal_live += 1
            exceptions.append({'order_no': order_no, 'cls': 'unverified', 'reason': 'no-chain-evidence',
                               'detail': 'status=%s tx=%s' % (o['status'], o['tx_hash'] or '')})
            continue

        # 4) 最小单位金额核对（按资产键分组）
        by_asset = {}
        for e in evs:
            key = e['asset_key'] or '?'
            by_asset.setdefault(key, 0)
            by_asset[key] = (by_asset[key] or 0) + (e['amount_units'] or 0)
        order_units = o['amount_units'] or 0
        asset_key = list(by_asset.keys())[0] if by_asset else '?'
        recv = by_asset.get(asset_key, 0)
        diff = recv - order_units
        if diff < 0:
            bump('underpaid')
            abnormal_live += 1
            exceptions.append({'order_no': order_no, 'cls': 'underpaid', 'reason': 'amount-diff<0',
                               'detail': 'order=%d recv=%d diff=%d %s' % (
                                   order_units, recv, diff, asset_key)})
            continue
        if diff > 0:
            bump('overpaid')
            abnormal_live += 1
            exceptions.append({'order_no': order_no, 'cls': 'overpaid', 'reason': 'amount-diff>0',
                               'detail': 'order=%d recv=%d diff=%d %s' % (
                                   order_units, recv, diff, asset_key)})
            continue

        # 5) 金额匹配：核验权益履约
        if order_no not in fulfilled:
            bump('pending_fulfillment')
            abnormal_live += 1
            exceptions.append({'order_no': order_no, 'cls': 'pending_fulfillment',
                               'reason': 'fulfillment-not-applied',
                               'detail': 'amount=%d %s evidence=%d' % (order_units, asset_key, len(evs))})
            continue

        # 6) 正常 matched（真实收入）
        bump('matched')
        matched_live += 1
        live_income[asset_key] = live_income.get(asset_key, 0) + order_units
        if order_no in callback_failed:
            # 到账与权益正常，但对外回执回调失败 → 附随异常（不隐藏）
            bump('callback_failed')
            exceptions.append({'order_no': order_no, 'cls': 'callback_failed',
                               'reason': 'callback-delivery-failed',
                               'detail': 'amount=%d %s' % (order_units, asset_key)})

    live_total = matched_live + abnormal_live
    return {
        'generated_at': now.isoformat() if not isinstance(now, str) else now,
        'scope': {
            'order_total': len(orders),
            'matched_live': matched_live,
            'abnormal_live': abnormal_live,
            'legacy': categories.get('legacy', 0),
            'stale': categories.get('stale', 0),
        },
        'live_income_by_asset': live_income,
        'live_success_rate': round(matched_live / live_total, 4) if live_total else None,
        'categories': categories,
        'exceptions': exceptions,
        'readonly': True,
    }


def render_report(report):
    """渲染报告为 JSON 字符串（scripts/reconcile.py 使用；只读）。"""
    return json.dumps(report, ensure_ascii=False, indent=2)

