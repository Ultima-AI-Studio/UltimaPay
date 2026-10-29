// ── pay_window_core.ts —— 支付窗口纯逻辑核心（UGP-20 抽离可测；PayWindow.tsx 仅薄渲染）──
// 仅使用可擦除 TS 语法（type/interface），可被 Node 22 type-stripping 直接执行做交互用例。
// 语义：
//  - 金额精度按链：ETH/TRON/SOL USDT = 6 位，BSC USDT = 18 位（parseUnits 不得混用 18 位）；
//  - 切链失败立即停止（4902=链未添加 → 提示后停止，不得忽略错误继续发送）；
//  - 二维码按链/资产协议生成（不把所有非 SOL 链套用 tron URI）；
//  - 本地 tx hash 不能自行变成功：只有服务端 confirmed 才确认；
//  - payment confirmed ≠ 权益已开通：履约未完成显示"开通处理中"，仅 fulfillment=done 才显示会员可用。

export type WindowStage = 'creating' | 'pending' | 'submitted' | 'confirming' | 'confirmed' | 'expired' | 'error';

export interface PayOrderData {
  orderNo: string;
  planName?: string;
  channel: string;
  network?: string;
  currency?: string;
  amountUsdt: number;
  tail?: number;
  status: string;
  address?: string;
  expiredAt?: string;
  txHash?: string;
  fulfillment?: string;   // UGP-19：pending / retry_required / done（done 由 UGP-27 写入）
}

export interface PayWindowApi {
  createOrder(user: string, planId: string, channel: string, idempotencyKey: string): Promise<PayOrderData>;
  getOrder(orderNo: string): Promise<PayOrderData>;
  confirmOrder(orderNo: string, txHash: string): Promise<PayOrderData>;
  channels(): Promise<Record<string, { address: string; enabled: boolean }>>;
}

// ── 金额精度（按链最小单位）──
export function assetDecimals(channel: string): number {
  return channel === 'BSC' ? 18 : 6;   // ETH/TRON/SOL = 6，BSC = 18
}

export function amountToUnits(amountUsdt: number, channel: string): number {
  return Math.round(amountUsdt * (10 ** assetDecimals(channel)));
}

// ── 切链：错误 → 立即停止（不忽略错误继续发送）──
export type SwitchOutcome = 'ok' | 'need_add_chain' | 'block';

export function switchChainOutcome(errCode: number | undefined): SwitchOutcome {
  if (errCode === undefined) return 'ok';
  if (errCode === 4902) return 'need_add_chain';   // 链未添加：提示添加，停止发送
  return 'block';                                   // 其他错误：停止并报错
}

// ── 二维码协议（按链生成）──
export function qrText(channel: string, address: string, amountUsdt: number): string {
  const amt = amountUsdt.toFixed(2);
  if (channel === 'SOL') return `solana:${address}?amount=${amt}`;
  if (channel === 'TRON') return `tron:${address}?amount=${amt}`;
  // ETH/BSC：EIP-681 风格（不再套用 tron URI）
  return `ethereum:${address}?value=${amt}u`;
}

// ── 回执结果：本地 tx hash 不能自行变成功 ──
export type ReceiptOutcome = 'pending_verification' | 'confirmed';

export function receiptOutcome(serverStatus: string, _txHash?: string): ReceiptOutcome {
  return serverStatus === 'confirmed' ? 'confirmed' : 'pending_verification';
}

// ── 状态展示：confirmed 不等于权益已开通 ──
export type DisplayStage = 'pending_verification' | 'confirming' | 'member_ready' | 'fulfilling' | 'expired' | 'error';

export function displayStage(paymentStatus: string, fulfillment?: string): DisplayStage {
  if (paymentStatus === 'confirmed') {
    return fulfillment === 'done' ? 'member_ready' : 'fulfilling';   // 履约未完成 → 开通处理中
  }
  if (paymentStatus === 'expired') return 'expired';
  return 'pending_verification';
}

// ── 幂等键：组件生命周期固定生成一次（重复挂载/切链/重试共用同一键 → 服务端单订单）──
export function makeIdempotencyKey(username: string, planId: string): string {
  return `ik_${username}_${planId}_${Math.random().toString(36).slice(2, 12)}`;
}

// ── 交互核心（状态机；api 注入，组件薄渲染）──
export interface PayCore {
  stage: WindowStage;
  order: PayOrderData | null;
  idempotencyKey: string;
  launch(ch?: string): Promise<WindowStage>;
  submitReceipt(tx: string): Promise<ReceiptOutcome>;
  poll(): Promise<WindowStage>;
}

export function createPayCore(cfg: { username: string; planId: string; api: PayWindowApi }): PayCore {
  let stage: WindowStage = 'creating';
  let order: PayOrderData | null = null;
  let channel = 'TRON';
  const idempotencyKey = makeIdempotencyKey(cfg.username, cfg.planId);

  async function launch(ch?: string): Promise<WindowStage> {
    stage = 'creating';
    channel = ch || channel;
    try {
      order = await cfg.api.createOrder(cfg.username, cfg.planId, channel, idempotencyKey);
      stage = 'pending';
    } catch (_e) {
      stage = 'error';
    }
    return stage;
  }

  async function submitReceipt(tx: string): Promise<ReceiptOutcome> {
    if (!order) return 'pending_verification';
    const co = await cfg.api.confirmOrder(order.orderNo, tx);
    order = co;
    const out = receiptOutcome(co.status, tx);
    stage = out === 'confirmed' ? 'confirmed' : 'submitted';
    return out;
  }

  async function poll(): Promise<WindowStage> {
    if (!order) return stage;
    const o = await cfg.api.getOrder(order.orderNo);
    order = o;
    if (o.status === 'confirmed') {
      stage = displayStage(o.status, o.fulfillment) === 'member_ready' ? 'confirmed' : 'confirming';
    } else if (o.status === 'expired') {
      stage = 'expired';
    }
    return stage;
  }

  return {
    get stage(): WindowStage { return stage; },
    get order(): PayOrderData | null { return order; },
    idempotencyKey, launch, submitReceipt, poll,
  };
}
