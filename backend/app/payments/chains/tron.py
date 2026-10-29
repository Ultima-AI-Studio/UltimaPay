# -*- coding: utf-8 -*-
"""payments/chains/tron —— TRON TRC-20 原始响应解析（UGP-18）

输入 TronGrid 原始响应项（dict），输出归一化链证据字段：
{tx_hash, contract, from, to, value(最小单位), block, block_time, confirmed, depth}
解析失败抛 ValueError（明确拒绝，不静默跳过）。"""
TRC20_TRANSFER_SIG = 'Transfer(address,address,uint256)'

_FIELDS = ('transaction_id', 'token_info', 'from', 'to', 'value',
           'block_number', 'block_timestamp', 'confirmed', 'contract_address',
           'type', 'raw_contract_address')


def _norm_addr(a):
    return (a or '').strip()


def parse_trc20_event(raw, contract_hint=None):
    """解析单条 TRC-20 事件。contract_hint 为权威合约地址（资产注册表），用于事件类型确认。
    返回归一化 dict；关键字段缺失/非 Transfer 事件 → ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError('TRON 原始响应必须是对象')
    tx_hash = _norm_addr(raw.get('transaction_id'))
    if not tx_hash:
        raise ValueError('缺少 transaction_id')
    contract = _norm_addr(raw.get('contract_address') or
                          (raw.get('token_info') or {}).get('address'))
    if not contract:
        raise ValueError('缺少合约地址')
    if raw.get('type') and raw['type'].upper() not in ('TRANSFER', 'TRC20_TRANSFER'):
        raise ValueError('非 Transfer 事件: %s' % raw.get('type'))
    if contract_hint and contract.lower() != (contract_hint or '').lower():
        raise ValueError('合约地址与权威资产不匹配')
    raw_value = raw.get('value')
    if raw_value is None:
        raise ValueError('缺少转账金额')
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError('金额不是整数最小单位: %r' % raw_value)
    if value < 0:
        raise ValueError('金额为负')
    from_ = _norm_addr(raw.get('from'))
    to = _norm_addr(raw.get('to'))
    if not from_ or not to:
        raise ValueError('缺少 from/to')
    confirmed = bool(raw.get('confirmed', True))
    block = raw.get('block_number')
    if block is not None:
        try:
            block = int(block)
        except (TypeError, ValueError):
            raise ValueError('block_number 非法')
    ts = raw.get('block_timestamp')
    if ts is not None:
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            raise ValueError('block_timestamp 非法')
    depth = raw.get('depth')
    if depth is not None:
        try:
            depth = int(depth)
        except (TypeError, ValueError):
            raise ValueError('depth 非法')
    return {'chain': 'tron', 'tx_hash': tx_hash, 'contract': contract,
            'from': from_, 'to': to, 'value': value, 'block': block,
            'block_time': ts, 'confirmed': confirmed, 'depth': depth}

