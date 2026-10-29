# -*- coding: utf-8 -*-
"""payments/settlement —— 落账（UGP-19）

record_verified_payment(db, order_no, evidence)：
- 同事务记录链证据（payment_ledger，tx_hash 唯一约束）、支付确认（payment_orders → confirmed）、
  待履约事件（payment_events outbox 入队 PaymentVerified）；
- 幂等重放：同一 tx_hash/证据已入账 → 直接返回已存在记录，不重复入账/重复入队；
- 网络回调（投递）不得夹在本事务内——入队后 commit，投递由 callbacks.deliver_callbacks 独立执行。
链事件唯一约束绑定网络/交易/事件：tx_hash 唯一（复用原 tx_hash 幂等要求）。
"""
import json
from datetime import datetime

from .outbox import enqueue_event, EVENT_VERIFIED


def _now_iso(now=None):
    return (now or datetime.now()).strftime('%Y-%m-%d %H:%M:%S')


def _evidence_payload(evidence):
    """ChainEvidence → payload dict（事件载荷；含 event 语义字段）。"""
    return {'order_no': evidence.order_no, 'tx_hash': evidence.tx_hash,
            'chain': evidence.chain, 'network': evidence.network,
            'asset_key': evidence.asset_key, 'amount_units': evidence.amount_units,
            'block': evidence.block, 'block_time': evidence.block_time,
            'confirmations': evidence.confirmations, 'finality': evidence.finality}


def record_verified_payment(db, order_no, evidence, callback_url=None, now=None):
    """原子落账＋PaymentVerified 事件。返回 (result, reason)：
    result='recorded'（新入账）/ 'duplicate'（幂等重放，未新增）/ 'order_not_found' / 'state_not_active'。"""
    now = now or datetime.now()
    # 幂等：同一 tx_hash 已入账 → 不重复入账、不重复事件
    dup = db.execute('SELECT id FROM payment_ledger WHERE tx_hash=?', (evidence.tx_hash,)).fetchone()
    if dup:
        return 'duplicate', '链事件已入账（tx_hash 幂等）'
    row = db.execute("SELECT status FROM payment_orders WHERE order_no=?",
                     (order_no,)).fetchone()
    if not row:
        return 'order_not_found', '订单不存在'
    if row[0] not in ('pending', 'submitted', 'confirming'):
        return 'state_not_active', '订单不在活动态（%s）' % row[0]
    payload = _evidence_payload(evidence)
    if callback_url:
        payload['callback_url'] = callback_url
    db.execute('BEGIN')
    try:
        db.execute("INSERT INTO payment_ledger(order_no, tx_hash, chain, network, asset_key, "
                   "amount_units, block, block_time, evidence_id, event_id, created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (order_no, evidence.tx_hash, evidence.chain, evidence.network,
                    evidence.asset_key, evidence.amount_units, evidence.block,
                    evidence.block_time, getattr(evidence, 'evidence_id', None),
                    None, _now_iso(now)))
        db.execute("UPDATE payment_orders SET status='confirmed', tx_hash=?, confirmed_at=?, source='live' "
                   "WHERE order_no=?", (evidence.tx_hash, _now_iso(now), order_no))
        ev = enqueue_event(db, order_no, EVENT_VERIFIED, payload, now=now)
        if ev['duplicate']:
            db.rollback()
            return 'duplicate', '事件已入队（幂等）'
        db.execute('UPDATE payment_ledger SET event_id=? WHERE tx_hash=?',
                   (ev['event_id'], evidence.tx_hash))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    return 'recorded', ev['event_id']

