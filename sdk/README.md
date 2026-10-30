# Ultima Pay SDK

Ultima Pay SDK 是 Ultima AI Studio 出品的 EVM 链上支付 SDK，提供**代币预售（IDO）购买、锁仓释放、推荐返佣**等链上交互能力。本 SDK 由 inpayment-sdk v1.1.8 完整迁移而来，并已修复迁移前遗留的若干问题（见 CHANGELOG）。

它是 **Ultima Pay** 产品线的链上分发子线，与 USDT TRC-20 扫码收款（链下订单 + 查链确认）互补，统一服务于 Ultima AI 旗下产品。

## USDT 聚合收款接入（五步）

服务地址通过部署环境配置；本地开发默认 `http://127.0.0.1:8061`。持久 `apiKey` 与
`secret` 只保存在商户服务端，不写入浏览器包、日志或审计明文。

1. 申请并领取：`POST /api/pay/apply` 提交 `code/name/email/callbackUrl` 及至少一个链地址；响应中的 `apiKey` 仅显示一次，立即安全保存。
2. 激活：以 `X-Pay-Key: <apiKey>` 调用 `POST /api/pay/verify`，状态变为 `active`。
3. 下单：商户服务端以同一 `X-Pay-Key` 调用 `POST /api/pay/merchant/intents`，取得短期 `sessionToken`；浏览器只使用该订单级 token。
4. 收款确认：UltimaPay 轮询链上原始交易，经最小单位、资产、收款方、深度和连续四轮核验后将订单置为 `confirmed`。
5. 接收回调：平台向申请时的 `callbackUrl` 投递 `payment.verified`；按 `event_id` 幂等，并用当前商户级 `secret` 校验 HMAC-SHA256 签名。管理员轮换 secret 时新值同样只显示一次，旧值立即失效。

最短服务端示例：

```js
const applied = await fetch(`${base}/api/pay/apply`, {
  method: 'POST', headers: {'content-type': 'application/json'},
  body: JSON.stringify({code: 'shop', name: 'Shop', email: 'ops@example.com',
    callbackUrl: 'https://shop.example/pay/callback', tronAddress: 'T...'})
}).then(r => r.json());
const apiKey = applied.data.apiKey; // 仅本次响应可见；写入服务端密钥库
await fetch(`${base}/api/pay/verify`, {method: 'POST', headers: {'X-Pay-Key': apiKey}});
```

### 回调密钥语义

| 订单类型 | 密钥 | 用途 |
|---|---|---|
| 外部商户订单 | 当前商户的 `secret` | 入站 `/api/pay/callback` 与出站 `payment.verified` 均只认该商户密钥 |
| 平台自有 `ultima-gold` 订单 | 平台 `callbackSecret` | 仅平台自有单使用，不能代替外部商户密钥 |

入站回执把请求 JSON 按键排序并使用紧凑分隔符序列化，再计算
`HMAC-SHA256(secret, canonicalJson)`，放入 `X-Pay-Signature`。出站事件签名串为
`event_id|order_no|payload`；接入方先验签，再按 `event_id` 幂等处理。

## 依赖要求

本 SDK 依赖 `ethers v6` 作为核心依赖，使用前请确保安装：

```bash
pnpm add ethers@^6.0.0
# 或
npm install ethers@^6.0.0
# 或
yarn add ethers@^6.0.0
```

## 安装

```bash
pnpm add ultima-pay-sdk
# 或
npm install ultima-pay-sdk
# 或
yarn add ultima-pay-sdk
```

## 初始化

### 申请成为项目方

1. 完成 Ultima Pay 项目方申请流程（合约部署与项目注册由 Ultima AI Studio 提供，详见产品线文档）
2. 审核通过后可创建项目，并复制 `projectId` 及 `projectRegistryAddress` 字段初始化 SDK 实例

### 初始化 SDK 实例

```typescript
import { UltimaPaySDK } from 'ultima-pay-sdk';
import { JsonRpcProvider, Wallet } from 'ethers';

// 初始化 provider
const provider = new JsonRpcProvider('https://your-rpc-url');

// 初始化 signer（用于发送交易）
const signer = new Wallet('your-private-key', provider);

// 或者使用 MetaMask 的 provider
// const signer = await provider.getSigner();

const sdk = new UltimaPaySDK({
  projectId: 'your-project-id',
  providerUrl: 'https://your-rpc-url',
  projectRegistryAddress: '0x...', // 项目注册合约地址
  priceFeedManagerAddress: '0x...', // 价格预言机合约地址
});

// 初始化SDK
await sdk.init();
```

## 类型定义

### UltimaPaySDKOptions

初始化SDK的选项：

```typescript
interface UltimaPaySDKOptions {
  projectId: string; // 项目ID
  providerUrl: string; // RPC节点URL
  projectRegistryAddress: string; // 项目注册合约地址
  priceFeedManagerAddress: string; // 价格预言机合约地址
}
```

### ProjectInfo

项目信息：

```typescript
interface ProjectInfo {
  projectOwner: string; // 项目方钱包地址
  tokenAddress: string; // 项目代币地址
  paymentProcessor: string; // 支付处理器合约地址
  vestingManager: string; // 锁仓管理器合约地址
  rounds: Round; // 预售轮次信息
  maxTokensToBuy: string; // 单个用户可以购买的最大代币数量
  isActive: boolean; // 是否激活
  createdAt: number; // 项目创建时间
  vestingConfig: VestingConfig; // 锁仓配置
  referralConfig: ReferralConfig; // 推荐配置
}
```

### Round

预售轮次信息：

```typescript
interface Round {
  tokenAmount: string; // 代币数量
  price: string; // 代币价格（USD标准，单位：wei），例如 0.1 USD = 100000000000000000
  startTime: number; // 开始时间（UNIX时间戳）
  endTime: number; // 结束时间（UNIX时间戳）
  dynamicPriceEnabled: boolean; // 价格调整启用
  priceIncreaseThreshold: string; // 销售比例阈值（百分比，如"50"表示50%）
  priceIncreaseRate: string; // 价格上调比例（百分比，如"10"表示10%）
}
```

### VestingConfig

锁仓配置：

```typescript
interface VestingConfig {
  enabled: boolean; // 是否启用
  vestingType: VestingType; // 锁仓类型
  cliff: number; // 悬崖期（单位：秒）
  duration: number; // 锁仓期（单位：秒）
  period: number; // 周期（单位：秒）- 用于周期释放
  periodReleasePercentage: string; // 每个周期的释放比例（百分比，如"10"表示10%）
}
```

### VestingType

锁仓类型枚举：

```typescript
enum VestingType {
  None = 0, // 不锁仓
  LINEAR = 1, // 线性释放
  STEP = 2, // 阶梯释放
}
```

### ReferralConfig

推荐配置：

```typescript
interface ReferralConfig {
  enabled: boolean; // 是否启用推荐功能
  referrerRewardRate: string; // 推荐人返点比例（如"20"表示20%）
  refereeDiscountRate: string; // 被推荐人折扣比例（如"10"表示10%）
}
```

### BuyTokensOptions

代币购买选项：

```typescript
interface BuyTokensOptions {
  amount: string | number; // 购买数量
  referrer?: string; // 推荐人地址（可选）
}
```

### TransactionResult

合约操作结果：

```typescript
interface TransactionResult {
  success: boolean; // 操作是否成功
  transactionHash?: string; // 交易哈希（可选）
  error?: string; // 错误信息（可选）
}
```

### VestingSchedule

锁仓计划：

```typescript
interface VestingSchedule {
  beneficiary: string; // 受益人地址
  amount: string; // 锁仓的代币总量
  released: string; // 已释放的代币数量
}
```

## 主要功能

### 1. 获取项目信息

```typescript
const projectInfo = sdk.getProjectInfo();
```

### 2. 使用ETH购买代币

```typescript
const result = await sdk.buyTokensWithETH(
  {
    amount: '1.0', // ETH数量
    referrer: '0x...', // 可选，推荐人地址
  },
  signer
);
```

### 3. 使用ERC20代币购买

```typescript
const result = await sdk.buyTokensWithToken(
  tokenAddress, // ERC20代币地址
  {
    amount: '100', // 代币数量
    referrer: '0x...', // 可选，推荐人地址
  },
  signer
);
```

### 4. 获取锁仓计划

```typescript
const schedule = await sdk.getVestingScheduleInfo({
  address: '0x...', // 地址
});
```

### 5. 释放代币

```typescript
const result = await sdk.releaseTokens(signer);
```

### 6. 获取可释放代币数量

```typescript
// 获取当前可释放的代币数量
const amount = await sdk.getReleaseAmount(signer);
```

### 7. 获取项目解锁时间

```typescript
// 获取项目的解锁时间信息
const unlockTimeInfo = await sdk.getUnlockTime();
// 返回值类型
interface UnlockTimeInfo {
  currentUnlockTime: number; // 当前周期的解锁时间（UNIX时间戳）
  unlockTimeList: number[]; // 所有解锁时间点列表（UNIX时间戳数组）
}
```

锁仓释放逻辑说明：

1. 自动释放（悬崖期）
   - 条件：项目开启自动释放（vestingConfig.enabled = true）
   - 第一次可领取时间 = 预售结束时间 + 悬崖期 + 周期
2. 手动释放（项目方指定）
   - 条件：项目方手动设置释放时间（releaseStartTime > 0）
   - 第一次可领取时间 = 项目方设置的释放时间 + 周期
3. 默认释放（30天后）
   - 条件：项目方未开启自动释放且未手动设置释放时间
   - 第一次可领取时间 = 预售结束时间 + 30天 + 周期

示例：
假设预售时间是 4.21 - 4.23，悬崖期 1 天，周期 5 天，每个周期释放 20%：

- 自动释放：4.23 + 1天 + 5天 = 4.29
- 手动释放（4.25开启）：4.25 + 5天 = 4.30
- 默认释放：4.23 + 30天 + 5天 = 5.28

### 8. 获取项目进度

```typescript
// 获取项目销售进度（返回百分比）
const progress = await sdk.getProjectProgress();
```

### 9. 获取代币USD价格

```typescript
// 获取指定代币的USD价格
const price = await sdk.getTokenUsdValue(tokenAddress);
```

### 10. 获取代币价格

```typescript
// 获取代币价格（包含折扣价格）
const { price, discountedPrice } = await sdk.getTokenPrice({
  buyer: '0x...', // 购买者地址
  referrer: '0x...', // 可选，推荐人地址
});
```

## 注意事项

1. 在使用 SDK 之前，请确保已经正确初始化
2. SDK 依赖 ethers v6，所有涉及交易的方法都需要提供有效的 `ethers.Signer` 实例
3. 如果您的项目使用的是 ethers v5，需要先升级到 v6
4. 建议在使用前先检查项目信息是否正确

## 示例代码

完整的示例代码可以在 `examples` 目录中找到。这些示例展示了如何使用 SDK 的各种功能。

---

Ultima Pay · Ultima AI Studio 出品
