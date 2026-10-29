# -*- coding: utf-8 -*-
"""
payments/api —— 支付 API 薄适配层（UGP-17）

从请求环境解析可信 principal（登录会话 / 商户凭据），委托 orders 域逻辑：
- create_payment_intent_endpoint：下单（幂等键 + 商品标识 + chain）；
- submit_receipt_endpoint：回执仅提交线索（进入待核验，异常进可审计队列）。
本模块不执行业务，仅做 principal 解析与端点接线；业务在 payments/orders。
"""
from ..auth.policy import principal_from_session, merchant_principal
from .orders import create_payment_intent, submit_receipt


def _resolve_principal(db, token, merchant=None, api_key=None):
    """可信会话优先；无会话时校验商户凭据（无效 → None，绝不回退官方商户）。"""
    p = principal_from_session(db, token)
    if p:
        return p, None
    if merchant and api_key:
        p, err = merchant_principal(db, merchant, api_key)
        if p:
            return p, None
        return None, err or '无效商户凭据'
    return None, '缺少可信会话或商户凭据'


def create_payment_intent_endpoint(db, token=None, merchant=None, api_key=None,
                                   product_ref=None, chain=None, idempotency_key=None,
                                   plan_id=None, receive_address=None):
    """端点处理器：解析 principal → orders.create_payment_intent。
    product_ref/chain/idempotency_key 必填；无效商户 key 直接拒绝。"""
    if not product_ref or not chain or not idempotency_key:
        return None, '缺少商品标识/支付链/幂等键'
    principal, err = _resolve_principal(db, token, merchant, api_key)
    if not principal:
        return None, err
    return create_payment_intent(db, principal, product_ref, chain, idempotency_key,
                                 plan_id=plan_id, merchant=merchant, receive_address=receive_address)


def submit_receipt_endpoint(db, order_no=None, tx_hash=None, amount_units=None,
                            token=None, merchant=None, api_key=None):
    """端点处理器：回执提交（仅线索）。"""
    if not order_no or not tx_hash:
        return None, '缺少订单号或交易哈希'
    principal, err = _resolve_principal(db, token, merchant, api_key)
    if not principal:
        return None, err
    return submit_receipt(db, order_no, tx_hash, amount_units=amount_units)

