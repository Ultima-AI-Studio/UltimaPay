"""Standalone payment API routes under the canonical `/api/pay` prefix."""

from __future__ import annotations

import io
import re
import secrets
import time
from datetime import datetime

import qrcode
from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, Response

from .. import payments
from ..auth import policy
from ..payments import merchant_api, orders as payment_orders
from ..storage import ensure_payment_base_schema, open_connection


router = APIRouter(prefix="/api/pay", tags=["payments"])


def _ok(data):
    return {"code": 0, "msg": "ok", "data": data}


def _error(status: int, message: str):
    return JSONResponse(status_code=status, content={"code": status, "msg": message, "data": None})


def _bearer(authorization: str | None) -> str:
    return authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""


def _admin_ok(authorization: str | None) -> bool:
    return policy.require_admin(_bearer(authorization))


def _db():
    db = open_connection()
    ensure_payment_base_schema(db)
    return db


@router.get("/qrcode")
def payment_qrcode(text: str = Query(default="")):
    db = _db()
    try:
        cfg = payments.read_payment_config(db)
    finally:
        db.close()
    image = qrcode.make(text or ("tron:" + cfg["address"]))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@router.get("/order")
def query_order(orderNo: str = Query(default="")):
    if not orderNo:
        return _error(400, "缺少 orderNo")
    db = _db()
    try:
        row = payments.read_payment_order(db, orderNo)
    finally:
        db.close()
    return _ok(row) if row else _error(404, "订单不存在")


@router.get("/channels")
def get_channels():
    db = _db()
    try:
        channels = payments.load_payment_channels(db)
    finally:
        db.close()
    return _ok({
        "channels": [
            {"key": key, "name": value["name"], "network": value["network"],
             "currency": value["currency"], "address": value.get("address", ""),
             "rpcUrl": value.get("rpcUrl", ""), "rpcBackup": value.get("rpcBackup", ""),
             "enabled": value.get("enabled", True)}
            for key, value in channels.items()
        ]
    })


@router.post("/channels")
def save_channels(body: dict, authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    db = _db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in body.get("channels") or []:
            key = str(item.get("key") or "")
            if key not in payments._PAYMENT_CHANNELS:
                continue
            db.execute(
                "INSERT INTO payment_channel_config(channel,address,enabled,rpc_url,rpc_backup,updated_at)"
                " VALUES(?,COALESCE((SELECT address FROM payment_channel_config WHERE channel=?),''),?,?,?,?)"
                " ON CONFLICT(channel) DO UPDATE SET enabled=excluded.enabled,"
                " rpc_url=excluded.rpc_url,rpc_backup=excluded.rpc_backup,updated_at=excluded.updated_at",
                (key, key, 1 if item.get("enabled", True) else 0,
                 str(item.get("rpcUrl") or "").strip(),
                 str(item.get("rpcBackup") or "").strip(), now),
            )
        db.commit()
    finally:
        db.close()
    return get_channels()


@router.get("/merchant-orders")
def merchant_orders(
    merchant: str = Query(default=""),
    status: str = Query(default="all"),
    api_key: str = Query(default=""),
    authorization: str | None = Header(default=None),
):
    if not merchant:
        return _error(400, "缺少 merchant 参数")
    db = _db()
    try:
        if _admin_ok(authorization):
            principal = {"role": "admin", "user_id": "admin", "username": "admin"}
        else:
            principal, reason = policy.merchant_principal(db, merchant, api_key)
            if not principal:
                return _error(401, reason or "未授权")
            allowed, reason = policy.authorize_object(principal, "read_own", "merchant", merchant)
            if not allowed:
                return _error(403, reason)
        rows, stats = payments.read_merchant_orders(db, merchant, status)
        policy.audit_sensitive(db, principal, "read", "merchant:orders", f"merchant={merchant} status={status}")
        return _ok({"list": rows, "stats": stats})
    finally:
        db.close()


@router.get("/tickets")
def get_tickets(status: str = Query(default=""), authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    db = _db()
    try:
        sql = ("SELECT id,ticket_no,merchant_code,merchant_name,contact_email,type,chain,old_address,"
               "new_address,reason,status,remark,created_at,updated_at FROM payment_tickets")
        args = []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        rows = db.execute(sql + " ORDER BY id DESC", args).fetchall()
        return _ok({"tickets": [
            {"id": row[0], "ticketNo": row[1], "merchantCode": row[2], "merchantName": row[3],
             "contactEmail": row[4], "type": row[5], "chain": row[6], "oldAddress": row[7],
             "newAddress": row[8], "reason": row[9], "status": row[10], "remark": row[11],
             "createdAt": row[12] or "", "updatedAt": row[13] or ""}
            for row in rows
        ]})
    finally:
        db.close()


def _merchant_payloads(rows):
    return [
        {"id": row[0], "code": row[1], "name": row[2], "email": row[3],
         "status": row[5], "applyAt": row[6] or "", "lastSeenAt": row[7] or "",
         "callbackUrl": row[8], "enabled": bool(row[10]), "remark": row[11],
         "createdAt": row[12], "tronAddress": row[13] or "", "ethAddress": row[14] or "",
         "bscAddress": row[15] or "", "solAddress": row[16] or ""}
        for row in rows
    ]


def _merchant_rows(db):
    return db.execute(
        "SELECT id,code,name,email,api_key,status,apply_at,last_seen_at,callback_url,secret,enabled,"
        "remark,created_at,tron_address,eth_address,bsc_address,sol_address"
        " FROM payment_merchants WHERE COALESCE(hidden,0)=0 ORDER BY id"
    ).fetchall()


@router.get("/merchants")
def get_merchants(authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    db = _db()
    try:
        return _ok({"merchants": _merchant_payloads(_merchant_rows(db))})
    finally:
        db.close()


@router.post("/merchants")
def save_merchant(body: dict, authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    code = str(body.get("code") or "").strip()
    name = str(body.get("name") or "").strip()
    if not code or not name:
        return _error(400, "code 与 name 必填")
    db = _db()
    try:
        current = db.execute(
            "SELECT api_key,status,tron_address,eth_address,bsc_address,sol_address,secret"
            " FROM payment_merchants WHERE code=?", (code,)
        ).fetchone()
        addresses = tuple(str(body.get(key) or "").strip() for key in
                          ("tronAddress", "ethAddress", "bscAddress", "solAddress"))
        current_addresses = tuple(current[index] or "" for index in range(2, 6)) if current else ()
        unprovisioned_seed = bool(current and not current[0] and not any(current_addresses))
        if current and not unprovisioned_seed and current_addresses != addresses:
            return _error(400, "收款地址已固化不可变更，如需换址请提交工单")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        api_key = current[0] if current and current[0] else "upay_live_" + secrets.token_hex(12)
        state = current[1] if current and current[0] else "active"
        secret = str(body.get("secret") or "").strip() if "secret" in body else (current[6] if current else "")
        stored_addresses = addresses if not current or unprovisioned_seed else current_addresses
        db.execute(
            "INSERT INTO payment_merchants(code,name,email,api_key,status,callback_url,secret,enabled,"
            "remark,created_at,tron_address,eth_address,bsc_address,sol_address)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(code) DO UPDATE SET name=excluded.name,email=excluded.email,"
            "api_key=excluded.api_key,status=excluded.status,callback_url=excluded.callback_url,"
            "secret=excluded.secret,enabled=excluded.enabled,remark=excluded.remark,"
            "tron_address=excluded.tron_address,eth_address=excluded.eth_address,"
            "bsc_address=excluded.bsc_address,sol_address=excluded.sol_address",
            (code, name, str(body.get("email") or "").strip(), api_key, state,
             str(body.get("callbackUrl") or "").strip(), secret,
             1 if body.get("enabled", True) else 0, str(body.get("remark") or "").strip(),
             now, *stored_addresses),
        )
        db.commit()
        return _ok({"merchants": _merchant_payloads(_merchant_rows(db))})
    finally:
        db.close()


@router.get("/address")
def payment_address():
    db = _db()
    try:
        cfg = payments.read_payment_config(db)
        return _ok({"address": cfg["address"], "network": "TRON TRC-20",
                    "currency": "USDT", "enabled": cfg["enabled"],
                    "ttlMinutes": cfg["ttlMinutes"]})
    finally:
        db.close()


@router.get("/orders")
def get_orders(status: str = Query(default="all"), authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    db = _db()
    try:
        return _ok({"list": payments.read_payment_orders(db, status)})
    finally:
        db.close()


@router.post("/apply")
def apply_merchant(body: dict):
    code = str(body.get("code") or "").strip().lower()
    name = str(body.get("name") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    if not code or not name or not email:
        return _error(400, "code、name、email 必填")
    if not re.match(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$", email):
        return _error(400, "邮箱格式不正确")
    addresses = tuple(str(body.get(key) or "").strip() for key in
                      ("tronAddress", "ethAddress", "bscAddress", "solAddress"))
    if not any(addresses):
        return _error(400, "请至少配置一条收款链路地址（TRON/ETH/BSC/SOL 任选）")
    db = _db()
    try:
        if db.execute("SELECT id FROM payment_merchants WHERE code=?", (code,)).fetchone():
            return _error(400, "该代号已被占用，请换一个")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        api_key = "upay_live_" + secrets.token_hex(12)
        db.execute(
            "INSERT INTO payment_merchants(code,name,email,api_key,status,apply_at,remark,created_at,"
            "tron_address,eth_address,bsc_address,sol_address) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, name, email, api_key, "applied", now,
             str(body.get("remark") or "").strip() or "网站自助申请", now, *addresses),
        )
        db.commit()
        row = db.execute(
            "SELECT id,code,name,email,status,apply_at FROM payment_merchants WHERE code=?", (code,)
        ).fetchone()
        return _ok({"id": row[0], "code": row[1], "name": row[2], "email": row[3],
                    "status": row[4], "applyAt": row[5],
                    "message": "申请成功，接入凭证已生成（SDK 接入包将发送至邮箱）"})
    finally:
        db.close()


@router.post("/verify")
def verify_merchant(body: dict, x_pay_key: str | None = Header(default=None)):
    api_key = str(x_pay_key or body.get("apiKey") or "").strip()
    if not api_key:
        return _error(401, "缺少接入凭证 X-Pay-Key")
    db = _db()
    try:
        row = db.execute(
            "SELECT id,code,name,status,enabled FROM payment_merchants WHERE api_key=?", (api_key,)
        ).fetchone()
        if not row:
            return _error(401, "接入凭证无效")
        if not row[4]:
            return _error(403, "接入方已停用")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE payment_merchants SET status='active',last_seen_at=? WHERE api_key=?", (now, api_key))
        db.commit()
        return _ok({"id": row[0], "code": row[1], "name": row[2],
                    "status": "active", "lastSeenAt": now})
    finally:
        db.close()


@router.post("/tickets/process")
def process_ticket(body: dict, authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    db = _db()
    try:
        row = db.execute(
            "SELECT id,merchant_code,chain,new_address,status FROM payment_tickets WHERE id=?",
            (int(body.get("id") or 0),),
        ).fetchone()
        if not row:
            return _error(404, "工单不存在")
        if row[4] in ("done", "rejected"):
            return _error(400, "工单已处理，不可重复操作")
        action = str(body.get("action") or "resolve")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if action == "change_address" and row[1] and row[2] and row[3]:
            column = {"TRON": "tron_address", "ETH": "eth_address",
                      "BSC": "bsc_address", "SOL": "sol_address"}.get(row[2])
            if not column:
                return _error(400, f"不支持的链：{row[2]}")
            db.execute(f"UPDATE payment_merchants SET {column}=? WHERE code=?", (row[3], row[1]))
        db.execute("UPDATE payment_tickets SET status='done',remark=?,updated_at=? WHERE id=?",
                   (str(body.get("remark") or "").strip() or "已处理", now, row[0]))
        db.commit()
        return _ok({"status": "done", "message": "工单已标记完成"})
    finally:
        db.close()


@router.post("/ticket")
def create_ticket(body: dict):
    code = str(body.get("merchantCode") or "").strip()
    email = str(body.get("contactEmail") or "").strip().lower()
    chain = str(body.get("chain") or "").strip().upper()
    new_address = str(body.get("newAddress") or "").strip()
    if not code or not email:
        return _error(400, "接入方代号与联系邮箱必填")
    if chain not in ("TRON", "ETH", "BSC", "SOL"):
        return _error(400, "仅支持 TRON/ETH/BSC/SOL 换址工单")
    if not new_address:
        return _error(400, "新收款地址必填")
    db = _db()
    try:
        merchant = db.execute(
            "SELECT name,tron_address,eth_address,bsc_address,sol_address"
            " FROM payment_merchants WHERE code=?", (code,)
        ).fetchone()
        if not merchant:
            return _error(404, "接入方不存在")
        old_address = merchant[{"TRON": 1, "ETH": 2, "BSC": 3, "SOL": 4}[chain]] or ""
        if not old_address:
            return _error(400, f"该接入方未配置 {chain} 收款链路")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ticket_no = "TK" + now.replace("-", "").replace(":", "").replace(" ", "") + str(int(time.time() * 1000) % 1000)
        db.execute(
            "INSERT INTO payment_tickets(ticket_no,merchant_code,merchant_name,contact_email,type,chain,"
            "old_address,new_address,reason,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,'open',?,?)",
            (ticket_no, code, str(body.get("merchantName") or "").strip() or merchant[0], email,
             "change_address", chain, old_address, new_address,
             str(body.get("reason") or "").strip() or "申请更换收款地址", now, now),
        )
        db.commit()
        return _ok({"ticketNo": ticket_no, "status": "open",
                    "message": "换址工单已提交，处理后将重新下发 SDK 接入包"})
    finally:
        db.close()


@router.post("/config")
def save_config(body: dict, authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    db = _db()
    try:
        saved = payments.save_payment_config(
            db, bool(body.get("enabled", True)), body.get("address") or "",
            body.get("ttlMinutes") or 30, body.get("callbackSecret") or ""
        )
        return _ok(saved)
    finally:
        db.close()


@router.post("/order")
def create_order(
    body: dict,
    x_pay_user: str | None = Header(default=None),
    x_pay_signature: str | None = Header(default=None),
    x_pay_key: str | None = Header(default=None),
):
    principal, reason = policy.user_principal(x_pay_user or "", x_pay_signature or "")
    if not principal:
        return _error(401, reason)
    db = _db()
    try:
        user = db.execute("SELECT username FROM users WHERE username=?", (principal["username"],)).fetchone()
        if not user:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO users(user_id,username,email,status,created_at) VALUES(?,?,?,?,?)",
                (principal["username"], principal["username"], "", "active", now),
            )
        merchant = "ultima-gold"
        if x_pay_key:
            row = db.execute(
                "SELECT code FROM payment_merchants WHERE api_key=? AND enabled=1", (x_pay_key,)
            ).fetchone()
            if not row:
                return _error(401, "商户凭据无效，不回退平台商户")
            merchant = row[0]
        raw_idempotency_key = str(body.get("idempotencyKey") or "").strip()
        idempotency_key = (
            f"user:{principal['username']}:{raw_idempotency_key}"
            if raw_idempotency_key else None
        )
        row, error = payment_orders.create_payment_intent(
            db,
            principal,
            payments.LEGACY_PRODUCT_REF,
            str(body.get("channel") or "TRON").upper(),
            idempotency_key,
            plan_id=body.get("planId") or "",
            merchant=merchant,
        )
        if error:
            return _error(400, error)
        item = payments.payment_row(row)
        cfg = payments.read_payment_config(db)
        channel = payments.load_payment_channels(db).get(item["channel"], {})
        return _ok({"orderNo": item["orderNo"], "username": item["username"],
                    "planId": item["planId"], "planName": item["planName"],
                    "channel": item["channel"], "network": channel.get("network", item["channel"]),
                    "currency": channel.get("currency", "USDT"), "amountUsdt": item["amountUsdt"],
                    "tail": item["tail"], "priceUsdt": item["priceUsdt"],
                    "discount": item["discount"], "status": item["status"],
                    "createdAt": item["createdAt"], "expiredAt": item["expiredAt"],
                    "address": item["receiveAddress"] or channel.get("address") or cfg["address"],
                    "ttlMinutes": cfg["ttlMinutes"]})
    finally:
        db.close()


@router.get("/user/orders")
def get_user_orders(
    x_pay_user: str | None = Header(default=None),
    x_pay_signature: str | None = Header(default=None),
):
    principal, reason = policy.user_principal(x_pay_user or "", x_pay_signature or "")
    if not principal:
        return _error(401, reason)
    db = _db()
    try:
        rows = db.execute(
            "SELECT * FROM payment_orders WHERE username=? ORDER BY id DESC LIMIT 50",
            (principal["username"],),
        ).fetchall()
        return _ok({"list": [payments.payment_row(row) for row in rows]})
    finally:
        db.close()


@router.post("/merchant/intents")
def create_merchant_intent(body: dict, x_pay_key: str | None = Header(default=None)):
    merchant = str(body.get("merchant") or "").strip()
    db = _db()
    try:
        principal, reason = policy.merchant_principal(db, merchant, x_pay_key or "")
        if not principal:
            return _error(401, reason or "商户凭据无效")
        order, session_token, error = merchant_api.create_merchant_intent(
            db,
            merchant,
            str(body.get("externalOrderNo") or "").strip(),
            str(body.get("externalUserRef") or "").strip(),
            body.get("amountUnits"),
            str(body.get("chain") or "").strip().upper(),
            str(body.get("idempotencyKey") or "").strip() or None,
        )
        if error:
            return _error(400, error)
        policy.audit_sensitive(
            db, principal, "create", "merchant:intent", f"order={order['order_no']}"
        )
        return _ok(
            {
                "orderNo": order["order_no"],
                "merchant": order["merchant"],
                "channel": order["channel"],
                "amountUnits": order["amount_units"],
                "receiveAddress": order["receive_address"],
                "status": order["status"],
                "expiredAt": order["expired_at"],
                "sessionToken": session_token,
            }
        )
    finally:
        db.close()


@router.get("/session/order")
def resolve_session_order(
    sessionToken: str = Query(default=""), orderNo: str = Query(default="")
):
    db = _db()
    try:
        order, error = merchant_api.resolve_pay_session(db, sessionToken, orderNo)
        if error:
            return _error(403, error)
        return _ok(
            {
                "orderNo": order["order_no"],
                "merchant": order["merchant"],
                "channel": order["channel"],
                "amountUnits": order["amount_units"],
                "receiveAddress": order["receive_address"],
                "status": order["status"],
                "expiredAt": order["expired_at"],
            }
        )
    finally:
        db.close()


@router.post("/create")
def admin_create_order(body: dict, authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    db = _db()
    try:
        row, error = payments.create_payment_order(
            db, body.get("user") or "smoke_user", body.get("planId") or "SP-01"
        )
        return _error(400, error) if error else _ok(payments.payment_row(row))
    finally:
        db.close()


@router.post("/callback")
def payment_callback(body: dict, x_pay_secret: str | None = Header(default=None)):
    db = _db()
    try:
        secret = payments.read_payment_config(db)["callbackSecret"]
        if not secret:
            return _error(403, "回调密钥未配置，拒绝处理回调")
        if not x_pay_secret or not secrets.compare_digest(x_pay_secret, secret):
            return _error(403, "回调密钥校验失败")
        amount = body.get("amountUsdt")
        row, error = payments.confirm_payment_order(
            db, body.get("orderNo") or "", body.get("txHash") or "",
            float(amount) if amount is not None else None, source="callback"
        )
        return _error(400, error) if error else _ok(row)
    finally:
        db.close()


@router.post("/confirm")
def confirm_payment(body: dict):
    db = _db()
    try:
        row, error = payments.confirm_payment_order(
            db, body.get("orderNo") or "", body.get("txHash") or "", None, "receipt"
        )
        return _error(400, error) if error else _ok(row)
    finally:
        db.close()


@router.post("/expire")
def expire_payment(body: dict, authorization: str | None = Header(default=None)):
    if not _admin_ok(authorization):
        return _error(401, "未授权：需要管理令牌")
    db = _db()
    try:
        cursor = db.execute(
            "UPDATE payment_orders SET status='expired' WHERE order_no=? AND status='pending'",
            (body.get("orderNo") or "",),
        )
        db.commit()
        return _ok({"affected": cursor.rowcount})
    finally:
        db.close()

