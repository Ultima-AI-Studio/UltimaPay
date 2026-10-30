# UltimaPay 业务闭环修复阶段报告

> 工单：`wo-ultimapay-20261030-0002`
> 状态：执行中
> 环境：Windows / PowerShell / Python venv；未使用真实资金或生产 RPC

## 修复前后对比

| 断裂点 | 修复前 | 修复后 |
|---|---|---|
| 链上确认与落账 | `payment_loop` 以浮点金额候选调用 `confirm_payment_order(source="poll")`，只能写 `submitted` | 轮询原始 provider 数据进入 `verify_receipt`，四轮及深度门通过后由 `record_verified_payment` 原子写 ledger、`confirmed/source=live` 与 outbox |
| Provider 数据 | 三链 fetch 仅返回浮点金额摘要，无法通过硬核验 | TRON/EVM/SOL 保留解析器需要的原始合约、最小单位、交易与最终性字段，并补充深度/区块时间 |

## Gate 记录

| Gate | 状态 | 证据 |
|---|---|---|
| T0 链上核验→落账→入队 | PASS | `evidence/close-loop/t0-gate.txt`：专项 10/10、后端全量 32/32；真实调用方 grep；旧浮点确认依据零命中 |
| T1 outbox→商户回调 | PASS | `evidence/close-loop/t1-gate.txt`：专项 4/4、后端全量 36/36；唯一投递调用方；旧重试器零命中 |
| T2 接入凭证交付 | PASS | `evidence/close-loop/t2-gate.txt`：apply→一次性 key→verify；callbackUrl 保存；admin 轮换；全量 37/37 |
| T3 回调密钥语义 | PASS | `evidence/close-loop/t3-gate.txt`：外部商户/平台自有正反签名；缺签名 401；全量 38/38 |
| T4 confirmed 端到端 | PASS | `evidence/close-loop/t4-gate.txt`：业务闭环 17/17；后端全量 39/39；reconciliation matched |

## 回调投递闭环

- 每轮 payment loop 在落账事务完成后领取 outbox 事件；网络 POST 不进入落账事务。
- HMAC-SHA256 密钥按订单归属解析，event_id 作为接入方幂等键。
- 成功置 delivered；失败指数退避；达到上限置 dead；缺 callback_url 记录明确失败原因。
- callback_status 独立记录，任何投递失败均不回滚 confirmed 订单。

## 新客户接入闭环

- `POST /api/pay/apply` 在成功响应中一次性返回 apiKey，并同时保存 callbackUrl；重复代号申请被拒，管理列表不回显明文。
- `POST /api/pay/verify` 使用领取到的 key 激活商户。
- 管理员列表仅展示 `apiKeyProvisioned/secretConfigured/canRotateSecret` 状态。
- `POST /api/pay/merchants/{code}/rotate-secret` 受 admin 鉴权，新 secret 仅本次响应返回，旧值立即失效；审计记录脱敏。
- `sdk/README.md` 已记录申请、激活、下单、确认、回调五步及最短示例。

## 密钥语义表

| 场景 | 订单归属 | 密钥权威源 | 签名与传输 |
|---|---|---|---|
| 入站 `POST /api/pay/callback` | 外部商户 | `payment_merchants.secret`（按订单 merchant 查找） | `X-Pay-Signature` = HMAC-SHA256(secret, 规范 JSON 请求体) |
| 入站 `POST /api/pay/callback` | 平台自有 `ultima-gold` | `payment_config.callback_secret` | 同上；商户 secret 不可替代 |
| 出站 `payment.verified` outbox | 外部商户 | `payment_merchants.secret` | HMAC-SHA256(secret, `event_id|order_no|payload`)；payload 携带 signature |
| 出站 `payment.verified` outbox | 平台自有 `ultima-gold` | `payment_config.callback_secret` | 同上；全局密钥仅服务平台自有单 |

密钥不得出现在日志、审计明文或管理员列表；apiKey/secret 仅在各自的一次性领取响应中显示。

## 边界声明

- 未修改 `payments/verification.py` 的硬核对、深度门或四轮确认语义。
- `confirm_payment_order` 的 receipt/manual/callback/poll 路径仍只写 `submitted`；confirmed 唯一入口为核验后原子落账。
- 未接真实资金、未做存量迁移、VPS 部署或套餐定价修改。

