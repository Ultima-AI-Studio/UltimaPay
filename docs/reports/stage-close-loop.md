# UltimaPay 业务闭环修复阶段报告

> 工单：`wo-ultimapay-20261030-0002`
> 状态：T0-Tn 本地验收通过并已创建冻结 tag；远端推送因网络环境阻塞
> 环境：Windows / PowerShell / Python venv；未使用真实资金或生产 RPC

## 修复前后对比

| 断裂点 | 修复前 | 修复后 |
|---|---|---|
| 链上确认与落账 | `payment_loop` 以浮点金额候选调用 `confirm_payment_order(source="poll")`，只能写 `submitted` | 轮询原始 provider 数据进入 `verify_receipt`，四轮及深度门通过后由 `record_verified_payment` 原子写 ledger、`confirmed/source=live` 与 outbox |
| Provider 数据 | 三链 fetch 仅返回浮点金额摘要，无法通过硬核验 | TRON/EVM/SOL 保留解析器需要的原始合约、最小单位、交易与最终性字段，并补充深度/区块时间 |

## Gate 记录

| Gate | 状态 | 证据 |
|---|---|---|
| T0 链上核验→落账→入队 | PASS | `evidence/close-loop/t0-gate.txt`：专项与 provider 契约；真实调用方 grep；旧浮点确认依据零命中 |
| T1 outbox→商户回调 | PASS | `evidence/close-loop/t1-gate.txt`：专项 4/4、后端全量 36/36；唯一投递调用方；旧重试器零命中 |
| T2 接入凭证交付 | PASS | `evidence/close-loop/t2-gate.txt`：apply→一次性 key→verify；callbackUrl 保存；admin 轮换；全量 37/37 |
| T3 回调密钥语义 | PASS | `evidence/close-loop/t3-gate.txt`：外部商户/平台自有正反签名；缺签名 401；全量 38/38 |
| T4 confirmed 端到端 | PASS | `evidence/close-loop/t4-gate.txt`：最终业务闭环 23/23；后端全量 45/45；reconciliation matched |
| Tn 总验收 | PASS（本地）/ 远端同步 BLOCKED | `evidence/close-loop/tn-gate.txt`：pytest 45/45、compileall、SDK 10/10 + build、PayWindow build、独立审查 Important 全修复；GitHub 连接 reset |

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

## 独立审查与有界修复

冻结前独立审查发现 7 项 Important，均以专项 RED→GREEN 在一次修复轮关闭：

1. 四轮 tracker 重新委托冻结的 `ConfirmationTracker`，按 provider 观察高度识别回退；候选消失或深度失格即清零。
2. EVM 缺可信区块时间时延后，不再绕过订单创建时间门。
3. TRON 标准账户事件无 block number 时，查询交易信息取得权威高度、成功状态和深度。
4. SOL 标准 `tokenAmount.amount` 归一为整数 amount，并从 `getAccountInfo` 验证 token account mint/owner 后提供 ATA 归属。
5. submitted 晚到账超过 `expired_at` 进入可审计 exception，不自动 confirmed。
6. HTTP 2xx 后 delivered 保持终态；订单 callback_status 记账异常不再把事件改回 failed。
7. outbox 以 SQLite `BEGIN IMMEDIATE` + processing 租约原子领取，重叠 worker 不再同时 POST 同一 pending event。

独立审查 Minor 延期：`sdk/README.md` 的 verify JavaScript 示例未显式发送 `{}` 请求体，FastAPI 会返回 422；不影响接口实现或已验证的 Python/HTTP 接入链路，留待纯文档修订。

## 最终验证摘要

| 命令 | 结果 |
|---|---|
| `backend/.venv/Scripts/python.exe -m pytest -q` | 45 passed in 2.22s |
| `backend/.venv/Scripts/python.exe -m compileall -q app migrations run.py` | exit 0 |
| `npm test`（sdk） | 1 file / 10 tests passed |
| `npm run build`（sdk） | CJS / ESM / DTS build success |
| `npm run build`（PayWindow 隔离干净 npm 布局） | TypeScript + Vite，1403 modules transformed，build success |

环境说明：本机 pnpm 在 H: 盘生成的混合 node_modules 会使 Vite 无法解析 Solana 的 browser buffer；按上一冻结阶段既有裁决，在 git 忽略的工单隔离目录以 npm 无锁干净布局验证。构建依赖目录已移出项目源目录，源码与 lockfile 未为环境问题改写。npm 安装报告 5 项传递依赖漏洞（1 low / 2 moderate / 2 critical），属于既有依赖树，未执行可能破坏版本约束的自动升级。

远端同步环境：仓库未配置 remote；按权威标准探测并实际推送 `https://github.com/Ultima-AI-Studio/UltimaPay.git`。本机 Git 配置的 `127.0.0.1:1080/1083` 代理均未监听，`gh api user` TLS 超时；命令级清空代理后的 `git push ... main --tags` 被远端链路 reset。因此本地 commit/tag 完成，远端 push 未完成，禁止表述为已同步。

## 边界声明

- 未修改 `payments/verification.py` 的硬核对、深度门或四轮确认语义。
- `confirm_payment_order` 的 receipt/manual/callback/poll 路径仍只写 `submitted`；confirmed 唯一入口为核验后原子落账。
- 未接真实资金、未做存量迁移、VPS 部署或套餐定价修改。

## 标准化回执

| 字段 | 回执 |
|---|---|
| 链路号 | `wo-ultimapay-20261030-0002` |
| 执行端 | Codex `/root` |
| 完成状态 | T0-Tn 本地 Gate 全部 PASS；本地冻结 tag 已创建；远端 push 因网络 reset 阻塞 |
| 根因结论 | 既有核验、落账与 outbox 模块未接入轮询业务，旧轮询只能写 submitted；商户凭证无交付渠道，入站/出站密钥与重试语义分裂 |
| 产出路径 | `backend/app/`、`backend/tests/test_t5_business_loop.py`、`sdk/README.md`、本报告及 `evidence/close-loop/` |
| 验收证据 | T0-T4 gate 文件 + `tn-gate.txt`；后端 45/45、SDK 10/10、双构建与 compileall |
| 越界声明 | 无 |
| 环境声明 | Windows/PowerShell；Python venv；Node npm 隔离构建；GitHub 直连 reset 且本地代理未运行；未使用真实资金/生产 RPC/VPS；未迁移存量数据 |

