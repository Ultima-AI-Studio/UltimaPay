// ── Ultima Pay 客户端 API 封装（对接聚合支付服务端点）──
// UGP-30：第三方前端仅持「短期限、订单作用域」支付会话；本文件不包含也不请求
// 任何持久商户密钥（api_key/secret 只存在于商户服务端），打包产物无秘密。

/** 独立服务基址；部署环境可在加载 SDK 前注入 window.__ULTIMAPAY_API__ 覆盖。 */
const runtimeBase = (globalThis as typeof globalThis & { __ULTIMAPAY_API__?: string })
  .__ULTIMAPAY_API__;
const API_BASE_URL = (runtimeBase || 'http://127.0.0.1:8061').replace(/\/$/, '');
const API = `${API_BASE_URL}/api/pay`;

export interface PayOrder {
  orderNo: string;
  username: string;
  planId: string;
  planName: string;
  channel: string;
  network: string;
  currency: string;
  amountUsdt: number;
  tail: number;
  priceUsdt: number;
  discount: number;
  status: 'pending' | 'submitted' | 'confirming' | 'confirmed' | 'expired';
  createdAt: string;
  expiredAt: string;
  address?: string;
  ttlMinutes?: number;
  txHash?: string;
  fulfillment?: 'pending' | 'retry_required' | 'done';   // UGP-19：付款确认 ≠ 权益已开通
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  const j = (await r.json()) as { code?: number; message?: string; data?: T };
  if (j.code !== 0) throw new Error(j.message || '请求失败');
  return j.data as T;
}

/** 拉起订单（金额由服务端锁死：套餐价 + 防撞尾数；幂等键由窗口生命周期固定 → 重复挂载/重试单订单） */
export function createOrder(
  user: string,
  userSignature: string,
  planId: string,
  channel = 'TRON',
  idempotencyKey?: string,
) {
  if (!userSignature) throw new Error('宿主应用未提供支付用户签名');
  return json<PayOrder>(`${API}/order`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Pay-User': user,
      'X-Pay-Signature': userSignature,
    },
    body: JSON.stringify({ user, planId, channel, idempotencyKey }),
  });
}

/** 订单状态查询（支付窗口轮询；返回 UGP-19 独立履约状态 fulfillment） */
export function getOrder(orderNo: string) {
  return json<PayOrder>(`${API}/order?orderNo=${encodeURIComponent(orderNo)}`);
}

/** 钱包签名成功后提交回执（UGP-03/17：只进入等待核验 submitted，不立即确认；到账由服务端链上核验流程完成） */
export function confirmOrder(orderNo: string, txHash: string) {
  return json<PayOrder>(`${API}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ orderNo, txHash }),
  });
}

/** 二维码地址（内容按链/资产协议生成：tron:/solana:/ethereum: URI + 精确金额） */
export function qrcodeUrl(order: PayOrder) {
  const text = order.channel === 'SOL'
    ? `solana:${order.address}?amount=${order.amountUsdt.toFixed(2)}`
    : order.channel === 'TRON'
      ? `tron:${order.address}?amount=${order.amountUsdt.toFixed(2)}`
      : `ethereum:${order.address}?value=${order.amountUsdt.toFixed(2)}u`;
  return `${API}/qrcode?text=${encodeURIComponent(text)}`;
}

/** 服务端启用通道；支付窗口据此点亮已配置链。 */
export function getChannels() {
  return json<{ channels: Array<{ key: string; address: string; enabled: boolean }> }>(
    `${API}/channels`,
  );
}
