# -*- coding: utf-8 -*-
"""payments/chains/evm —— EVM（ETH/BSC）Transfer 日志原始响应解析（UGP-18）

输入 eth_getLogs / getTransactionReceipt 原始响应（dict），提取 Transfer 事件：
topic0=0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
topic1=from（32 字节右对齐地址）、topic2=to、data=value(uint256)。
返回归一化链证据字段；关键字段缺失/非 Transfer → ValueError（明确拒绝）。"""
import binascii

TRANSFER_TOPIC0 = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'


def _addr_from_topic(topic):
    """topic 32 字节 → 0x 地址（取末 20 字节）。"""
    t = (topic or '').strip().lower()
    if not t.startswith('0x'):
        t = '0x' + t
    t = t[2:].zfill(64)
    return '0x' + t[-40:]


def _value_from_data(data):
    d = (data or '').strip().lower()
    if d.startswith('0x'):
        d = d[2:]
    if not d:
        return 0
    try:
        return int(d, 16)
    except ValueError:
        raise ValueError('data 不是合法 uint256')


def _block_int(v, name):
    if v is None:
        return None
    try:
        return int(str(v), 16) if isinstance(v, str) and v.startswith(('0x', '0X')) else int(v)
    except (TypeError, ValueError):
        raise ValueError('%s 非法' % name)


def parse_evm_log(raw, contract_hint=None, chain=None):
    """解析单条 Transfer log。raw 为日志对象（含 address/topics/data/blockNumber/logIndex/
    transactionHash/removed）；status 由 receipt 层提供（0x1 成功）。"""
    if not isinstance(raw, dict):
        raise ValueError('EVM 日志必须是对象')
    contract = (raw.get('address') or '').strip().lower()
    if not contract:
        raise ValueError('缺少日志合约地址')
    topics = raw.get('topics') or []
    if not topics or topics[0].lower() != TRANSFER_TOPIC0:
        raise ValueError('非 Transfer 事件（topic0 不匹配）')
    if contract_hint and contract != (contract_hint or '').lower():
        raise ValueError('合约地址与权威资产不匹配')
    if len(topics) < 3:
        raise ValueError('Transfer 事件缺少 from/to topic')
    from_ = _addr_from_topic(topics[1])
    to = _addr_from_topic(topics[2])
    value = _value_from_data(raw.get('data'))
    tx_hash = (raw.get('transactionHash') or raw.get('transaction_hash') or '').strip().lower()
    if not tx_hash:
        raise ValueError('缺少 transactionHash')
    if raw.get('removed'):
        raise ValueError('日志已被重组移除')
    block = _block_int(raw.get('blockNumber') or raw.get('block_number'), 'blockNumber')
    log_index = _block_int(raw.get('logIndex') or raw.get('log_index'), 'logIndex')
    ts = raw.get('block_time') or raw.get('timestamp')
    if ts is not None:
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            raise ValueError('block_time 非法')
    return {'chain': (chain or 'evm').lower(), 'tx_hash': tx_hash, 'contract': contract,
            'from': from_, 'to': to, 'value': value, 'block': block,
            'log_index': log_index, 'block_time': ts,
            'confirmed': bool(raw.get('confirmed', True)),
            'depth': raw.get('depth')}

