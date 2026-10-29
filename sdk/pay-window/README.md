# Ultima Pay · 支付窗口（前端 SDK 组件）

**产品无关的可嵌入支付窗口**：需求方产品传入「用户 + 宿主签名 + 套餐/商品 ID」，组件自动完成
「拉起订单（金额服务端锁死）→ 选链 → 钱包直付 / 二维码兜底 → 轮询确认 → 结果回传」。

技术栈：React 18 + Chakra UI + Vite + TypeScript（复用原 examples 骨架，重构为收款场景）。

## 快速接入

```tsx
import PayWindow from './components/PayWindow';

<PayWindow
  username={user.uid}        // 用户标识（官网由登录态传入）
  userSignature={user.paySignature} // 宿主服务端签发；不得在浏览器保存签名密钥
  planId="SP-02"             // 套餐/商品 ID（金额由服务端按此锁定）
  planName="季卡"             // 展示名（可选）
  onResult={(r) => {
    // r = { status: 'confirmed'|'expired'|'pending', orderNo, txHash? }
    console.log('支付结果', r);
  }}
/>
```

组件内部流程：

1. 宿主服务端签发当前用户身份，通过 `userSignature` 传入；SDK 不持有签名密钥
2. `POST /api/pay/order` 拉起订单 → 服务端锁定金额（套餐价 + 防撞尾数，唯一）
3. 检测已装钱包扩展（`window.tronWeb` 等）→ 就绪则「唤起钱包支付」签名转账
4. 无钱包 → 自动降级：显示收款二维码（含精确金额）+ 地址复制
5. 每 5s 轮询 `GET /api/pay/order?orderNo=` → confirmed/expired 收敛
6. 钱包直付成功后提交回执（`POST /api/pay/confirm`，只进入待链上核验）

## 组件结构

```
src/
├── api.ts                     # 服务端点封装（下单/查询/确认/二维码）
├── components/
│   └── PayWindow.tsx          # 核心支付窗口组件（单文件，可直接复制进任何 React 工程）
└── App.tsx                    # 模拟购买 demo（选套餐 → 下一步 → 支付窗口）
```

## 依赖的服务端点（由 UltimaPay 独立服务提供，默认 `http://127.0.0.1:8061`）

| 端点 | 方法 | 用途 |
| --- | --- | --- |
| `/api/pay/order` | POST | 自助下单：`{user, planId, channel}` → 锁死金额订单 + 收款地址 |
| `/api/pay/order?orderNo=` | GET | 订单状态查询（轮询） |
| `/api/pay/confirm` | POST | 钱包回执提交（到账仍由链上核验确认） |
| `/api/pay/qrcode?text=` | GET | 二维码（支持 `tron:地址?amount=金额` 精确转账） |
| `/api/pay/channels` | GET | 通道配置（地址/启停/超时） |

## 链支持（动态钱包通道）

组件挂载时自动探测浏览器已装钱包扩展（`window.ethereum` / `window.tronWeb` / `window.solana`），
**检测到的钱包对应通道点亮可选，未检测到则置灰不提供该钱包的支付入口**；TRON 保留二维码兜底（未装 TronLink 也可扫码转账）。

| 通道 | 钱包检测 | 无钱包时 | 状态 |
| --- | --- | --- | --- |
| TRON（USDT TRC-20） | `window.tronWeb`（TronLink） | 二维码/地址兜底可用 | ✅ 钱包直付已实现 |
| Ethereum（USDT ERC-20） | `window.ethereum`（MetaMask 等聚合钱包） | 置灰不可选 | 📋 服务端 EVM 收款地址待配置 |
| BSC（USDT BEP-20） | `window.ethereum`（MetaMask 切链） | 置灰不可选 | 📋 服务端 EVM 收款地址待配置 |
| Solana（USDT SPL） | `window.solana`（Phantom） | 置灰不可选 | 📋 适配器接入中 |

配置好 EVM 收款地址后，EVM 通道将自动点亮并支持 MetaMask 切链直付（`wallet_switchEthereumChain` + USDT ERC-20/BEP-20 转账）。

## 验证状态

- ✅ 构建：`pnpm build`（tsc + vite）通过
- ✅ 最小闭环联调（浏览器实测）：选季卡 → 拉起订单 458.xx（含尾数）→ 未装钱包自动降级二维码 → 模拟回执核销 → 前端轮询收敛「支付成功」→ onResult 回传
- ⚠️ **验证缺口**：钱包直付的「真实签名」链路需浏览器安装 TronLink 扩展后才能实测（代码路径：检测 `window.tronWeb.ready` → `contract().at(USDT_TRC20).transfer(to, amountSun).send()`）；当前自动化环境无扩展，已验证至「检测 + 降级」。

## 本地运行

```bash
pnpm install
pnpm dev    # http://localhost:5178（proxy /api/pay → http://127.0.0.1:8061）
```

Ultima AI Studio · 2026
