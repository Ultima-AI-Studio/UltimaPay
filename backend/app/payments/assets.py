# -*- coding: utf-8 -*-
"""
payments/assets —— UGP-16 资产注册表（chain+network+contract/mint+decimals 定位）

- 资产由 chain+network+contract/mint+decimals 唯一定位，**不按 USDT 符号判定**（symbol 仅展示）。
- 测试网与主网完全区分（同 chain 不同 network 是不同资产键）。
- ETH USDT ERC-20=6 位、BSC USDT BEP-20=18 位、TRON USDT TRC-20=6 位、SOL USDT SPL=6 位
  （与 payments/__init__.py EVM_USDT / USDT_SPL_MINT 既有事实一致，统一收敛到本注册表）。
"""
from .money import parse_amount_units, format_amount_units, amount_to_report  # noqa: F401

# 主网注册表：key = chain@network:contract/mint
_ASSETS = {
    'ETH@mainnet:0xdAC17F958D2ee523a2206206994597C13D831ec7': {
        'chain': 'eth', 'network': 'mainnet', 'symbol': 'USDT',
        'contract': '0xdAC17F958D2ee523a2206206994597C13D831ec7', 'decimals': 6, 'enabled': True},
    'BSC@mainnet:0x55d398326f99059fF775485246999027B3197955': {
        'chain': 'bsc', 'network': 'mainnet', 'symbol': 'USDT',
        'contract': '0x55d398326f99059fF775485246999027B3197955', 'decimals': 18, 'enabled': True},
    'TRON@mainnet:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t': {
        'chain': 'tron', 'network': 'mainnet', 'symbol': 'USDT',
        'contract': 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t', 'decimals': 6, 'enabled': True},
    'SOL@mainnet:Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': {
        'chain': 'sol', 'network': 'mainnet', 'symbol': 'USDT',
        'mint': 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', 'decimals': 6, 'enabled': True},
}

# 测试网注册表（与主网完全区分；供测试/沙箱注入）
_TEST_ASSETS = {
    'ETH@sepolia:0xdAC17F958D2ee523a2206206994597C13D831ec7': {
        'chain': 'eth', 'network': 'sepolia', 'symbol': 'USDT',
        'contract': '0xdAC17F958D2ee523a2206206994597C13D831ec7', 'decimals': 6, 'enabled': True},
    'BSC@testnet:0x337610d27c682E347C9cD60bd4b341b43F28c48f': {
        'chain': 'bsc', 'network': 'testnet', 'symbol': 'USDT',
        'contract': '0x337610d27c682E347C9cD60bd4b341b43F28c48f', 'decimals': 18, 'enabled': True},
    'TRON@shasta:TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj': {
        'chain': 'tron', 'network': 'shasta', 'symbol': 'USDT',
        'contract': 'TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj', 'decimals': 6, 'enabled': True},
    'SOL@devnet:Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': {
        'chain': 'sol', 'network': 'devnet', 'symbol': 'USDT',
        'mint': 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', 'decimals': 6, 'enabled': True},
}


def register_asset(chain, network, contract_or_mint, decimals, symbol='USDT', enabled=True, test=False):
    """注册/覆盖资产（测试注入测试链用；test=True 写入测试网注册表）。"""
    reg = _TEST_ASSETS if test else _ASSETS
    key = '%s@%s:%s' % (chain.lower(), network.lower(), contract_or_mint)
    reg[key] = {'chain': chain.lower(), 'network': network.lower(), 'symbol': symbol,
                'contract': contract_or_mint, 'decimals': int(decimals), 'enabled': bool(enabled)}
    return key


def _match(reg, chain, network, contract_or_mint=None):
    chain = chain.lower()
    network = network.lower()
    for k, a in reg.items():
        if a['chain'] != chain or a['network'] != network:
            continue
        c = a.get('contract') or a.get('mint')
        if contract_or_mint is None or (c or '').lower() == contract_or_mint.lower():
            return dict(a)
    return None


def resolve_asset(chain, network, contract_or_mint=None, symbol=None, test=False):
    """按 chain+network+contract/mint 定位资产。不按 symbol 判定（symbol 仅供展示过滤）。
    未知组合 → None。test=True 时先查测试网注册表。"""
    reg = _TEST_ASSETS if test else _ASSETS
    a = _match(reg, chain, network, contract_or_mint)
    if a is None and test:
        a = _match(_ASSETS, chain, network, contract_or_mint)
    if a is None:
        return None
    if symbol is not None and a['symbol'].upper() != symbol.upper():
        return None   # 仅作展示一致性校验，不参与定位
    return a


def decimals_for(chain, network, contract_or_mint=None, test=False):
    a = resolve_asset(chain, network, contract_or_mint, test=test)
    return a['decimals'] if a else None


def all_assets(test=False):
    reg = _TEST_ASSETS if test else _ASSETS
    return [dict(v) for v in reg.values()]

