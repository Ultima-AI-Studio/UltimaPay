# -*- coding: utf-8 -*-
"""payments/verification —— UGP-18 服务端链上真实性核验（纯逻辑，fixture/回放驱动）

verify_receipt(intent, chain, provider_result, db=None, tracker=None, now=None)
→ (ChainEvidence|None, status, reason)

- status: confirmed / pending（待更多确认）/ rejected（明确拒绝）/ unknown（RPC 结果不可信）。
- 硬核对（wrong_asset_chain_recipient_or_amount_denied）：
  资产（contract/mint 与权威注册表一致，同名伪资产拒绝）、链/网络、收款方（to/ATA 归属）、
  精确金额（最小单位整数相等，非浮点模糊）、交易成功状态、订单时间窗、交易事件唯一性。
- 确认门（confirmation_gate_and_reorg）：
  深度 TRON≥19 / EVM≥12（provider depth/confirmations），SOL 按最终性；
  连续确认 ≥4 轮，每轮必须是有间隔的新观察（block 前进、时间前进）；
  block 回退/重组 → 撤回候选（重置）；RPC timeout/null/失败/未知结果不得推进成功。
"""
from .assets import resolve_asset
from .chains import tron as tron_chain
from .chains import evm as evm_chain
from .chains import solana as solana_chain

CONFIRMATION_ROUNDS = 4
DEPTH_MIN = {'tron': 19, 'eth': 12, 'bsc': 12, 'sol': 0}   # SOL 按最终性（finality 字段）
CLOCK_SKEW_TOLERANCE = 60   # 交易时间早于订单创建可容忍秒数（时钟偏差）


class ChainEvidence(object):
    """C4 ChainEvidence：服务端验证产生的可信证据（仅此可推进到账状态）。"""

    def __init__(self, order_no, tx_hash, chain, network, asset_key, amount_units,
                 from_addr, to_addr, block, block_time, confirmations, finality):
        self.order_no = order_no
        self.tx_hash = tx_hash
        self.chain = chain
        self.network = network
        self.asset_key = asset_key
        self.amount_units = amount_units
        self.from_addr = from_addr
        self.to_addr = to_addr
        self.block = block
        self.block_time = block_time
        self.confirmations = confirmations
        self.finality = finality

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}


class ConfirmationTracker(object):
    """连续确认轮次状态机：保存区块标识识别重组/回退；有间隔的新观察累计 ≥4 轮 → confirmed。
    - observe(block_ref, ts)：block_ref 回退（< 上次）→ 撤回重置；ts 未前进 → 不算新轮。
    - confirmed()：累计轮次 ≥ rounds 且无回退。"""

    def __init__(self, rounds=CONFIRMATION_ROUNDS):
        self.rounds = rounds
        self._obs = []   # [(block_ref, ts)]

    def observe(self, block_ref, ts=None):
        if self._obs:
            last_ref, last_ts = self._obs[-1]
            if block_ref is None:
                return len(self._obs)
            if last_ref is not None and block_ref < last_ref:
                self._obs = []          # 重组/回退 → 撤回候选
                return 0
            if ts is not None and last_ts is not None and ts <= last_ts:
                return len(self._obs)   # 非新观察（无间隔/时间未前进）
        self._obs.append((block_ref, ts))
        return len(self._obs)

    @property
    def count(self):
        return len(self._obs)

    def confirmed(self):
        return len(self._obs) >= self.rounds

    def reset(self):
        self._obs = []


def _parse_provider(chain, provider_result, asset):
    """逐链解析原始响应 → 归一化证据 dict；解析失败抛 ValueError（明确拒绝）。"""
    if chain == 'tron':
        return tron_chain.parse_trc20_event(provider_result,
                                            contract_hint=asset.get('contract'))
    if chain in ('eth', 'bsc', 'evm'):
        return evm_chain.parse_evm_log(provider_result,
                                       contract_hint=asset.get('contract'), chain=chain)
    if chain == 'sol':
        return solana_chain.parse_solana_tx(provider_result, mint_hint=asset.get('mint'))
    raise ValueError('不支持的链 %s' % chain)


def _normalize_asset(intent, chain):
    """按 chain+network+contract/mint 定位权威资产；定位失败 → 拒绝。"""
    contract_or_mint = None
    if chain in ('eth', 'bsc', 'tron'):
        contract_or_mint = intent.get('contract') or intent.get('asset_key', '').split(':', 1)[-1]
    elif chain == 'sol':
        contract_or_mint = intent.get('mint') or intent.get('asset_key', '').split(':', 1)[-1]
    network = intent.get('network') or 'mainnet'
    asset = resolve_asset(chain, network, contract_or_mint)
    if asset is None:
        raise ValueError('链/网络/资产未登记（资产键 %s）' % (intent.get('asset_key') or contract_or_mint))
    return asset


def _unique_event_ok(db, order_no, tx_hash, amount_units):
    """交易事件唯一性：同 tx_hash 已绑定其他订单 → 拒绝（重复证明不得跨订单复用）。"""
    if db is None:
        return True
    row = db.execute(
        "SELECT order_no FROM payment_orders WHERE tx_hash=? AND order_no != ? LIMIT 1",
        (tx_hash, order_no)).fetchone()
    return row is None


def verify_receipt(intent, chain, provider_result, db=None, tracker=None, now=None):
    """服务端核验：硬核对 → 深度门 → 连续确认轮次 → ChainEvidence。
    tracker 为 None 时新建（单次观察不确认）；每次调用累计一次新观察。"""
    order_no = intent.get('order_no')
    if not order_no:
        return None, 'rejected', '缺少订单号'
    try:
        asset = _normalize_asset(intent, chain)
        evidence = _parse_provider(chain, provider_result, asset)
    except ValueError as e:
        return None, 'rejected', '%s' % e
    # 1) 资产/链/网络核对
    expected_contract = (asset.get('contract') or asset.get('mint') or '').lower()
    actual_contract = (evidence.get('contract') or evidence.get('mint') or '').lower()
    if actual_contract != expected_contract:
        return None, 'rejected', '资产不匹配（同名伪资产）'
    if evidence['chain'] not in (chain, 'evm' if chain in ('eth', 'bsc') else chain):
        return None, 'rejected', '链不匹配'
    # 2) 收款方核对（to 精确；SOL 校验 ATA 归属收款方）
    receive = (intent.get('receive_address') or '').lower()
    to = (evidence.get('to') or '').lower()
    if chain == 'sol':
        ata_for = (evidence.get('ata_for') or '').lower()
        if not ata_for or ata_for != receive:
            return None, 'rejected', '收款地址不匹配（ATA 归属非订单收款方）'
    elif not to or to != receive:
        return None, 'rejected', '收款地址不匹配'
    # 3) 精确金额核对（最小单位整数相等）
    amount_units = intent.get('amount_units')
    if amount_units is None or int(evidence.get('value', -1)) != int(amount_units):
        return None, 'rejected', '金额不匹配（精确最小单位）'
    # 4) 交易成功状态（解析器已拒绝失败交易；此处确认标志核对）
    if evidence.get('confirmed') is False:
        return None, 'rejected', '交易未确认/失败'
    if chain == 'sol' and evidence.get('finality') not in ('confirmed', 'finalized'):
        return None, 'rejected', 'SOL 交易最终性不足'
    # 5) 订单时间窗：交易不得早于订单创建（容忍时钟偏差），也不得超出订单窗口
    created = intent.get('created_at')
    bt = evidence.get('block_time')
    if created and bt is not None:
        try:
            import datetime
            created_ts = datetime.datetime.strptime(created[:19], '%Y-%m-%d %H:%M:%S').timestamp()
        except ValueError:
            created_ts = None
        if created_ts is not None and bt < created_ts - CLOCK_SKEW_TOLERANCE:
            return None, 'rejected', '交易早于订单创建（时间窗不匹配）'
    # 6) 交易事件唯一性
    tx_hash = evidence.get('tx_hash')
    if not _unique_event_ok(db, order_no, tx_hash, amount_units):
        return None, 'rejected', '交易事件已绑定其他订单'
    # 7) 深度门（TRON≥19 / EVM≥12；SOL 按最终性已核）
    depth = evidence.get('depth')
    if chain in DEPTH_MIN and DEPTH_MIN[chain] > 0:
        if depth is None:
            return None, 'pending', '深度不足：缺少确认深度（%s≥%d）' % (chain, DEPTH_MIN[chain])
        if depth < DEPTH_MIN[chain]:
            return None, 'pending', '深度不足：%d < %d' % (depth, DEPTH_MIN[chain])
    # 8) 连续确认轮次（有间隔新观察；重组撤回）
    tracker = tracker or ConfirmationTracker()
    n = tracker.observe(evidence.get('block'), bt)
    if not tracker.confirmed():
        return None, 'pending', '连续确认不足：%d/%d 轮' % (n, CONFIRMATION_ROUNDS)
    finality = 'confirmed' if tracker.confirmed() else 'pending'
    ev = ChainEvidence(order_no, tx_hash, chain, intent.get('network') or 'mainnet',
                       asset['chain'] + '@' + asset['network'] + ':' + expected_contract,
                       int(amount_units), evidence.get('from'), evidence.get('to') or evidence.get('ata_for'),
                       evidence.get('block'), evidence.get('block_time'),
                       tracker.count, finality)
    return ev, 'confirmed', '服务端链上核验通过（%d 轮连续确认）' % tracker.count

