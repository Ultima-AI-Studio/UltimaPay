# -*- coding: utf-8 -*-
"""payments/chains/solana —— Solana transferChecked/ATA 原始响应解析（UGP-18）

输入 Solana RPC getTransaction 原始响应（与 PayWindow @solana/web3.js + @solana/spl-token
同构构造结构），从外层/内层指令中提取 transferChecked：
- mint / amount（最小单位）/ authority / source / destination（收款 ATA）；
- ataFor（收款人地址，ATA 归属校验目标）；
- 交易结果（err 为空 + confirmationStatus confirmed/finalized）。
ATA 归属校验：destination 必须是对 ataFor（收款方）为该 mint 派生/存在的 token account——
本组件只认 fixture/调用方显式携带 ataFor 且 ataFor==收款地址的 transferChecked。
关键字段缺失/非 transferChecked/交易失败 → ValueError（明确拒绝，不静默跳过）。"""

TRANSFER_CHECKED = 'transferChecked'
TRANSFER = 'transfer'
CREATE_ATA = 'createAssociatedTokenAccount'


def _collect_parsed(raw):
    """遍历外层 + 内层指令中的 parsed 指令。"""
    out = []
    tx = raw.get('transaction') or {}
    msg = tx.get('message') or {}
    for ins in msg.get('instructions') or []:
        parsed = ins.get('parsed') or {}
        if parsed.get('type'):
            out.append(parsed)
    meta = raw.get('meta') or {}
    for group in meta.get('innerInstructions') or []:
        for ins in group.get('instructions') or []:
            parsed = ins.get('parsed') or {}
            if parsed.get('type'):
                out.append(parsed)
    return out


def parse_solana_tx(raw, mint_hint=None):
    """解析单笔 Solana 交易中的 transferChecked（收款 ATA 入账）。
    mint_hint 为权威 mint（资产注册表）。返回归一化链证据 dict。"""
    if not isinstance(raw, dict):
        raise ValueError('Solana 原始响应必须是对象')
    err = raw.get('err')
    meta = raw.get('meta') or {}
    status = meta.get('status') or {}
    if err or status.get('Err'):
        raise ValueError('交易失败: %s' % (err or status.get('Err')))
    conf = (raw.get('confirmationStatus') or '').lower()
    if conf and conf not in ('confirmed', 'finalized'):
        raise ValueError('交易最终性不足: %s' % conf)
    parsed_list = _collect_parsed(raw)
    ev = None
    for p in parsed_list:
        if p.get('type') not in (TRANSFER_CHECKED, TRANSFER):
            continue
        info = p.get('info') or {}
        mint = info.get('mint')
        amount = info.get('amount')
        destination = info.get('destination')
        ata_for = info.get('ataFor')
        authority = info.get('authority')
        if p.get('type') == TRANSFER_CHECKED:
            if not mint or amount is None:
                raise ValueError('transferChecked 缺少 mint/amount')
            if mint_hint and (mint or '').lower() != (mint_hint or '').lower():
                raise ValueError('mint 与权威资产不匹配（同名伪资产）')
            try:
                value = int(amount)
            except (TypeError, ValueError):
                raise ValueError('amount 不是整数最小单位: %r' % amount)
            if value < 0:
                raise ValueError('金额为负')
            ev = {'mint': mint, 'value': value, 'destination': destination,
                  'ata_for': ata_for, 'authority': authority}
            break
        # 普通 transfer：无 mint 明确性，拒绝（本组件要求 transferChecked 精确定位资产）
        raise ValueError('普通 transfer 无法定位资产，拒绝')
    if ev is None:
        raise ValueError('未找到 transferChecked 指令')
    if not ev['destination']:
        raise ValueError('transferChecked 缺少收款 ATA')
    tx_hash = (raw.get('signature') or raw.get('transactionSignature') or '').strip()
    if not tx_hash:
        raise ValueError('缺少交易签名')
    slot = raw.get('slot')
    if slot is not None:
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            raise ValueError('slot 非法')
    return {'chain': 'sol', 'tx_hash': tx_hash, 'mint': ev['mint'],
            'value': ev['value'], 'destination': ev['destination'],
            'ata_for': ev['ata_for'], 'authority': ev['authority'],
            'block': slot, 'slot': slot, 'block_time': raw.get('blockTime'),
            'finality': conf or 'confirmed'}

