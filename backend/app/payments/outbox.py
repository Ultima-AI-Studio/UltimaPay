# -*- coding: utf-8 -*-
"""payments/outbox —— 待办事件 outbox（UGP-19）

- enqueue_event：入队（同订单同事件类型唯一 → 幂等重放不重复入队）；event_id 唯一（出站回调幂等键）。
- claim_events：领取到期待投递事件（有界批次；失败退避 next_retry_at 到期才可重领）。
- mark_delivered / mark_failed：投递结果回写；失败有界重试（attempts 超 max → dead 死信队列）。
网络回调（投递动作）由 callbacks 模块执行，不得夹在落账数据库事务中。
"""
import json
import uuid
from datetime import datetime, timedelta

EVENT_VERIFIED = 'payment.verified'

_RETRY_BASE_SEC = 10


def _now_iso(now=None):
    return (now or datetime.now()).strftime('%Y-%m-%d %H:%M:%S')


def _fmt_dt(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def enqueue_event(db, order_no, event_type, payload, now=None):
    """入队事件（幂等：同订单同事件类型已存在 → 返回已有事件不重复入队）。
    payload 为 dict（JSON 序列化存储）。返回 {event_id, order_no, event_type, status}。"""
    now = now or datetime.now()
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    row = db.execute('SELECT event_id, status FROM payment_events '
                     'WHERE order_no=? AND event_type=?', (order_no, event_type)).fetchone()
    if row:
        return {'event_id': row[0], 'order_no': order_no, 'event_type': event_type,
                'status': row[1], 'duplicate': True}
    event_id = 'evt_' + uuid.uuid4().hex
    db.execute('INSERT INTO payment_events(event_id, order_no, event_type, payload, status, '
               'attempts, max_attempts, next_retry_at, last_error, created_at) '
               'VALUES(?,?,?,?,?,?,?,?,?,?)',
               (event_id, order_no, event_type, payload_json, 'pending', 0,
                8, _fmt_dt(now), None, _fmt_dt(now)))
    return {'event_id': event_id, 'order_no': order_no, 'event_type': event_type,
            'status': 'pending', 'duplicate': False}


def claim_events(db, batch=10, now=None):
    """原子领取到期待投递事件；processing 以 60 秒租约防并发重复投递。
    返回 [{id, event_id, order_no, event_type, payload, attempts}]；无到期项返回空。"""
    now = now or datetime.now()
    now_text = _fmt_dt(now)
    lease_until = _fmt_dt(now + timedelta(seconds=60))
    db.execute('BEGIN IMMEDIATE')
    try:
        rows = db.execute(
            "SELECT id, event_id, order_no, event_type, payload, attempts FROM payment_events "
            "WHERE ((status IN ('pending','failed') AND "
            "(next_retry_at IS NULL OR next_retry_at <= ?)) "
            "OR (status='processing' AND next_retry_at <= ?)) "
            "AND attempts < max_attempts ORDER BY id LIMIT ?",
            (now_text, now_text, batch)).fetchall()
        for row in rows:
            db.execute(
                "UPDATE payment_events SET status='processing', next_retry_at=? WHERE id=?",
                (lease_until, row[0]),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    out = []
    for r in rows:
        try:
            payload = json.loads(r[4])
        except (TypeError, ValueError):
            payload = {}
        out.append({'id': r[0], 'event_id': r[1], 'order_no': r[2],
                    'event_type': r[3], 'payload': payload, 'attempts': r[5]})
    return out


def mark_delivered(db, event_id, now=None):
    db.execute("UPDATE payment_events SET status='delivered', delivered_at=?, last_error=NULL, "
               "next_retry_at=NULL "
               "WHERE event_id=?", (_fmt_dt(now or datetime.now()), event_id))
    db.commit()
    return True


def mark_failed(db, event_id, error, now=None, max_attempts=None):
    """投递失败：attempts+1；next_retry_at 指数退避（10s * 2^(attempts-1)）；超 max → dead 死信。"""
    now = now or datetime.now()
    row = db.execute('SELECT attempts, max_attempts FROM payment_events WHERE event_id=?',
                     (event_id,)).fetchone()
    attempts = (row[0] if row else 0) + 1
    cap = max_attempts if max_attempts is not None else (row[1] if row else 8)
    if attempts >= cap:
        db.execute("UPDATE payment_events SET status='dead', attempts=?, last_error=? WHERE event_id=?",
                   (attempts, '%s' % error, event_id))
    else:
        delay = _RETRY_BASE_SEC * (2 ** (attempts - 1))
        retry_at = now + timedelta(seconds=delay)
        db.execute("UPDATE payment_events SET status='failed', attempts=?, last_error=?, next_retry_at=? "
                   "WHERE event_id=?", (attempts, '%s' % error, _fmt_dt(retry_at), event_id))
    db.commit()
    return attempts

