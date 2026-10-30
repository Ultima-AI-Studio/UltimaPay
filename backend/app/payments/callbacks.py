# -*- coding: utf-8 -*-
"""payments/callbacks —— 出站回调投递（UGP-19）

- deliver_callbacks：领取 outbox 事件 → 构造载荷（event_id + 认证签名 HMAC-SHA256）→
  网络 POST（可注入 http_post，默认 urllib）→ 成功 mark_delivered / 失败 mark_failed（有界重试+退避）；
- 回调失败 ≠ 未到账：payment_orders 已 confirmed 不回滚；独立 callback/fulfillment 状态；
- fulfillment_status：payment_status（orders）与 callback_status / fulfillment（权益应用，UGP-27 接入）
  分开——confirmed 不等于权益已开通；业务未处理显示 pending，回调失败显示 retry_required。
接入方必须按 event_id 幂等（载荷携带 event_id + 签名，重复投递由接入方去重）。
"""
import hashlib
import hmac
import json
import urllib.request

from .outbox import claim_events, mark_delivered, mark_failed


def sign_payload(secret, event_id, order_no, payload_json):
    """认证签名：HMAC-SHA256(secret, canonical(event_id|order_no|payload))。"""
    canonical = '%s|%s|%s' % (event_id, order_no, payload_json)
    return hmac.new((secret or '').encode('utf-8'), canonical.encode('utf-8'),
                    hashlib.sha256).hexdigest()


def sign_inbound_callback(secret, payload):
    """HMAC-SHA256 over canonical JSON for the inbound receipt endpoint."""
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    )
    return hmac.new(
        (secret or '').encode('utf-8'), canonical.encode('utf-8'), hashlib.sha256
    ).hexdigest()


def _default_post(url, data, timeout=10):
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def _callback_secret(db, order_no):
    row = db.execute(
        "SELECT merchant FROM payment_orders WHERE order_no=?", (order_no,)
    ).fetchone()
    merchant = row[0] if row else ""
    if merchant == "ultima-gold":
        config = db.execute(
            "SELECT callback_secret FROM payment_config WHERE id=1"
        ).fetchone()
        return (config[0] if config else "") or ""
    secret = db.execute(
        "SELECT secret FROM payment_merchants WHERE code=?", (merchant,)
    ).fetchone()
    return (secret[0] if secret else "") or ""


def _set_callback_status(db, order_no, status):
    db.execute(
        "UPDATE payment_orders SET callback_status=? WHERE order_no=?",
        (status, order_no),
    )
    db.commit()


def _record_failure(db, ev, error, now):
    mark_failed(db, ev['event_id'], error, now=now)
    status = db.execute(
        "SELECT status FROM payment_events WHERE event_id=?", (ev['event_id'],)
    ).fetchone()[0]
    _set_callback_status(db, ev['order_no'], status)
    return status


def deliver_callbacks(db, batch=10, http_post=None, now=None):
    """投递一批到期事件。http_post(url, body_bytes) 可注入（测试用）。
    密钥按事件所属订单解析；返回 delivered/failed/dead 统计。"""
    http_post = http_post or _default_post
    evs = claim_events(db, batch=batch, now=now)
    stats = {'delivered': 0, 'failed': 0, 'dead': 0}
    for ev in evs:
        payload = dict(ev['payload'])
        payload['event_id'] = ev['event_id']
        payload['order_no'] = ev['order_no']
        secret = _callback_secret(db, ev['order_no'])
        if not secret:
            status = _record_failure(db, ev, '缺少 callback secret', now)
            stats[status] += 1
            continue
        payload['signature'] = sign_payload(secret, ev['event_id'], ev['order_no'],
                                            json.dumps(payload, ensure_ascii=False, sort_keys=True))
        url = (payload.get('callback_url') or '').strip()
        if not url:
            status = _record_failure(db, ev, '缺少 callback_url', now)
            stats[status] += 1
            continue
        try:
            status = http_post(url, json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            event_status = _record_failure(db, ev, '%s' % e, now)
            stats[event_status] += 1
            continue
        if not 200 <= int(status) < 300:
            event_status = _record_failure(db, ev, 'HTTP %s' % status, now)
            stats[event_status] += 1
            continue
        mark_delivered(db, ev['event_id'], now=now)
        stats['delivered'] += 1
        try:
            _set_callback_status(db, ev['order_no'], 'success')
        except Exception:
            # The remote already acknowledged this event.  delivered is terminal;
            # local summary bookkeeping must never turn it into a retry.
            pass
    return stats


def fulfillment_status(db, order_no):
    """独立状态：payment_status（orders）/ callback_status（事件投递）/ fulfillment（权益应用）。
    confirmed 不等于权益已开通；独立服务只报告履约台账状态，不加载产品会员模块。
    未 applied 不显示会员已可用。"""
    order = db.execute(
        'SELECT plan_id, status FROM payment_orders WHERE order_no=?', (order_no,)
    ).fetchone()
    if not order:
        return {'payment_status': 'not_found'}
    event = db.execute(
        'SELECT status FROM payment_events WHERE order_no=? ORDER BY id DESC LIMIT 1',
        (order_no,),
    ).fetchone()
    fulfillment = db.execute(
        'SELECT status, expires_at FROM fulfillment_log WHERE order_no=? ORDER BY id DESC LIMIT 1',
        (order_no,),
    ).fetchone()
    return {
        'order_no': order_no,
        'payment_status': order[1],
        'plan_id': order[0],
        'callback_status': event[0] if event else 'none',
        'fulfillment': fulfillment[0] if fulfillment else 'pending',
        'expiresAt': fulfillment[1] if fulfillment else None,
        'entitlement': None,
    }

