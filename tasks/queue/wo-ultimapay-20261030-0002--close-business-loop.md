# Codex 工单 · 阶段业务闭环修复（业务能力闭环打通）

> 版本：v1.0 ｜ 状态：可开工（前置：wo-ultimapay-20261029-0001-frozen 已闭合）｜ 适用工具：Codex
> 用法：按 T0→Tn 顺序逐条执行，Gate PASS 后冻结再进下一条。
> 范围依据：本单为对 `wo-ultimapay-20261029-0001`（拆分闭环）的独立业务审计后补建的业务闭环修复；审计结论见 `docs/reports/split-v1-audit.md`；本单修复审计发现的 2 处闭环断裂 + 2 处不一致。

---

## 0. 范围冻结

**本单只做**：
1. 接通「链上确认→落账→履约通知」主链路：payment_loop 轮询到账 → 严格核验（verification.verify_receipt）→ 原子落账（settlement.record_verified_payment）→ outbox 事件投递（callbacks.deliver_callbacks）→ 商户回调送达。
2. 打通新客户接入凭证交付：自助申请后 api_key 可领取/可下发；管理员可查看/轮换商户密钥；申请时收集 callbackUrl。
3. 统一回调密钥语义（商户级 secret 与全局 callbackSecret 的关系明确化）。
4. 补 confirmed 链路端到端测试（当前测试只到 submitted）。
5. 依赖清理：确认 `verify_receipt`/`record_verified_payment`/`deliver_callbacks` 三个已写但无调用方的模块完成真实接线（grep 证明当前仅定义+测试引用，无业务调用）。

**本单不做**：
- 不做真实链上资金流转、不接真实 RPC 生产端点（公共端点可用，配置属部署范畴）
- 不动套餐定价（catalog.py 保持现状）
- 不迁移存量生产数据
- 不做 VPS/Nginx 部署（另单）
- 不重写 Gold 侧（UltimaGold 已是 Pay 显式消费者，本单只保证 Pay 服务自身闭环）
- 不改变订单状态机语义与止血规则（confirmed 仍仅由链上核验写入）

---

## 1. 任务清单

| 任务 | 文件 | 验收门 |
|---|---|---|
| T0 | services/payment_loop.py + payments/__init__.py | 轮询路径真实调用核验→落账→入队，订单可到 confirmed |
| T1 | payments/callbacks.py + services/payment_loop.py | outbox 事件真实投递商户回调，成功/失败/重试闭环 |
| T2 | routes/payment.py + payments/merchant_api.py | 新客户接入凭证可领取/可下发/可轮换，申请收集 callbackUrl |
| T3 | payments/callbacks.py + payments/__init__.py | 回调密钥语义统一（商户级为主，全局为平台自有单） |
| T4 | tests/ | confirmed 链路端到端测试全绿（T0-T3 的 Gate 证据测试） |
| Tn | 总验收 | 全量测试 + 双构建 + stage-close-loop.md + wo-ultimapay-20261030-0002-frozen |

---

## 2. 任务明细（每条 T 严格三段式）

### 上下文（所有 T 共享，先读）

- 项目引导索引：`H:\UltimaAI\UltimaPay\docs\INDEX.md`（若存在先读，定位项目规范）
- 业务审计结论：`H:\UltimaAI\UltimaPay\docs\reports\split-v1-audit.md`
- 拆分阶段报告：`H:\UltimaAI\UltimaPay\docs\reports\stage-split-v1.md`
- 代码位置：`H:\UltimaAI\UltimaPay\backend\app\`
- 当前已知断裂（grep 证据，勿重复调查）：
  - `verification.verify_receipt`（核验）→ `settlement.record_verified_payment`（落账）→ `callbacks.deliver_callbacks`（outbox 投递）**无业务调用方**
  - `payment_loop._poll_and_settle` 调 `confirm_payment_order(source="poll")`，该函数 onchain 分支直接返回"链上核验流程尚未启用"→ 订单永远停在 submitted，永远到不了 confirmed → 收款确认/权益履约/对账 matched/商户回调全部不可达
  - `deliver_merchant_callback` 要求 status=confirmed 才发回调 → 当前永不触发
  - `/api/pay/apply` 生成 api_key 后只返回"SDK 接入包将发送至邮箱"，无发邮件动作、无领取接口；管理员 merchants 列表 payload（`_merchant_payloads`）不含 api_key/secret；`rotate_merchant_secret` 无路由暴露
  - `/api/pay/callback` 校验全局 `callbackSecret`（payment_config），出站回调用商户 secret 签名——两套密钥体系语义不一致

### T0 · 接通链上核验→落账→入队主链路

**文件**：
- `backend/app/services/payment_loop.py`
- `backend/app/payments/__init__.py`（若 confirm_payment_order 保留则标注废弃或改造；不得同时保留两条确认路径）
- `backend/app/payments/verification.py`（如需要补充 intent 组装，可改，但核验逻辑本身冻结）
- `backend/app/payments/settlement.py`（核验成功后调用 record_verified_payment）

**写什么**：
1. 改造 `payment_loop._poll_and_settle`（或等价轮询函数）：对每条链上入账交易，按订单组装核验 intent（order_no/chain/receive_address/amount_units/asset_key/created_at），调用 `verification.verify_receipt` 做严格核验（资产/链/收款方/精确最小单位/时间窗/交易唯一性/深度门 TRON≥19 EVM≥12 SOL finality/连续 4 轮确认）。
2. 核验返回 ChainEvidence + status=confirmed 后，调用 `settlement.record_verified_payment` 原子落账：写入 payment_ledger（tx_hash 幂等）、订单置 confirmed（source='live'）、PaymentVerified 事件入 outbox；返回 duplicate 时跳过不重复入账。
3. 保留资金安全底线：`confirm_payment_order` 的 receipt/callback/poll 路径继续只写 submitted（止血规则不变）；**confirmed 唯一入口 = 链上核验通过**。若保留旧函数，明确标注 deprecated 并在其 docstring 写"confirmed 仅由 verify_receipt→record_verified_payment 链路写入"。
4. `verify_receipt` 的 provider_result 来源：轮询已 fetch 到的原始交易数据（TRON trc20 事件 / EVM log / SOL parsed tx）按各链 `payments/chains/*.py` 的 parse 函数归一化后传入；不要在轮询层再写一套浮点近似匹配（现 `abs(amount_usdt-?)<0.005` 匹配必须移除或降级为候选预筛选，最终金额以最小单位精确核对为准）。
5. 轮询循环每轮输出 heartbeat（livePolling/verified/recorded/duplicate/expired），保持异步 asyncio 结构不变。

**验收（Gate T0）**：
1. grep 证明 `verify_receipt` 与 `record_verified_payment` 均有真实业务调用方（非仅测试）。
2. 单元/集成测试：构造链上核验通过场景，订单可从 pending → confirmed，且 payment_ledger 有记录、payment_events 有 PaymentVerified 事件、status source='live'。
3. 构造核验拒绝场景（金额不匹配/收款方不对/深度不足/交易早于订单创建）→ 订单不得 confirmed。
4. tx_hash 重复入账 → 返回 duplicate，不重复写 ledger/事件。
5. 现有 T1 回执路径（submit_receipt → submitted）行为不回退。
6. 旧浮点匹配 `abs(amount_usdt-?)<0.005` 不再作为确认依据。

**禁止**：
- 禁止绕过核验直接写 confirmed
- 禁止在轮询层用浮点金额直接确认
- 禁止修改 verification.py 的硬核对/深度门/确认轮次语义
- 禁止删除 submitted 止血路径

### T1 · 打通 outbox 事件投递→商户回调

**文件**：
- `backend/app/services/payment_loop.py`
- `backend/app/payments/callbacks.py`（如需要）
- `backend/app/payments/__init__.py`（deliver_merchant_callback 若与 outbox 投递重复，统一到 callbacks.py 的 outbox 链路）

**写什么**：
1. payment_loop 每轮调用 `callbacks.deliver_callbacks`（领取 pending/failed 到期事件 → HMAC 签名 → POST → mark_delivered/mark_failed，指数退避，attempts≥max → dead 死信），与现 `retry_payment_callbacks`（旧商户回调重试）合并或明确替代，消除两套回调重试机制。
2. 出站回调 URL 与密钥来源统一：事件 payload 携带 callback_url（record_verified_payment 支持传入）；密钥=商户级 secret（商户表），平台自有单（ultima-gold）用 payment_config.callbackSecret；签名=HMAC-SHA256(event_id|order_no|payload)，见 callbacks.sign_payload。
3. 回调失败 ≠ 未到账：orders 已 confirmed 不回滚；callback_status 独立记录；接入方按 event_id 幂等。
4. 每轮输出 callback 投递统计（delivered/failed/dead）。

**验收（Gate T1）**：
1. 集成测试：confirmed 订单 + callback_url → 事件被投递（可注入 http_post 模拟），成功 mark_delivered、失败 mark_failed 且退避重试、超限进 dead。
2. 无 callback_url → 不阻塞，事件标记失败原因"缺少 callback_url"（或按现逻辑不投递但可审计）。
3. 回调失败后订单保持 confirmed（不回滚）。
4. 旧 `retry_payment_callbacks` 与新链路无重复投递（同 event_id 只投一次成功）。

**禁止**：
- 禁止回调失败回滚订单/撤销权益
- 禁止重复投递同一 event_id 成功两次
- 禁止把网络投递动作夹进落账 DB 事务

### T2 · 新客户接入凭证交付闭环

**文件**：
- `backend/app/routes/payment.py`
- `backend/app/payments/merchant_api.py`
- `backend/app/payments/__init__.py`（rotate_merchant_secret 已存在，接路由）
- `backend/app/db.py` 或对应 migration（如商户表需加 callback_url 收集字段，检查是否已存在）

**写什么**：
1. 自助申请 `/api/pay/apply`：生成 api_key 后提供**一次性领取**响应（返回 api_key 明文仅此一次，或提供领取接口 `GET /api/pay/merchants/me/key` 需携带申请回执凭据）——二选一实现，杜绝"生成后无法领取"；同时收集商户 callbackUrl（可选字段），状态仍为 applied。
2. 管理员侧：`GET /api/pay/merchants` payload 增加商户密钥管理入口（不直接回显 api_key 明文，改为返回"密钥状态/可轮换"标记）；新增/暴露 `POST /api/pay/merchants/<code>/rotate-secret` 管理路由（require_admin），调用 `rotate_merchant_secret`，新 secret 仅本次响应返回（一次性领用）。
3. 商户 `/api/pay/verify` 激活逻辑保持（用 api_key 激活 → active + last_seen_at），但补文档/响应说明：applied 商户凭一次领取得到 api_key 后才能 verify。
4. 申请→领取→verify→下单→收回调 全链路在 README（`docs/` 或 `sdk/README.md` 上层）补接入说明：超短示例代码 + 各步骤凭据位置。

**验收（Gate T2）**：
1. 接口测试：apply 后能领取到 api_key（且只有一次明文返回）；凭 key verify 成功变 active；未领取前 verify 失败。
2. 管理路由：管理员可轮换商户 secret，旧值立即失效（merchant_secret_ok 对旧值 False），新值本次返回。
3. 申请时可保存 callbackUrl，商户回调以该 URL 为目标。
4. 未授权（非 admin）调 rotate-secret → 403/401 拒绝。
5. 接入文档步骤完整（申请/领取/激活/下单/回调五步）。

**禁止**：
- 禁止 api_key/secret 明文持久化到日志或审计记录（audit_sensitive 的 raw_secrets 脱敏）
- 禁止在响应里无限次回显 api_key
- 禁止绕过 admin 鉴权暴露密钥管理

### T3 · 统一回调密钥语义

**文件**：
- `backend/app/routes/payment.py`（`/api/pay/callback` 入口）
- `backend/app/payments/__init__.py` / `callbacks.py`

**写什么**：
1. 明确并落文档：**商户订单回调密钥 = 商户级 secret（payment_merchants.secret）**；**平台自有单（merchant=ultima-gold）回调密钥 = payment_config.callbackSecret**。
2. `/api/pay/callback`（管理端/平台回执入口）校验：若订单属外部商户 → 按该商户 secret 校验签名；若平台自有单 → 按全局 callbackSecret 校验。禁止对所有订单只用全局密钥。
3. 在 `docs/reports/stage-close-loop.md` 记录密钥语义表，并在 SDK/接入文档同步说明。

**验收（Gate T3）**：
1. 测试：外部商户订单回调用商户 secret 签名 → 通过；用全局 secret 签名 → 拒绝。
2. 平台自有单用全局 secret → 通过；商户 secret → 拒绝。
3. 无签名/错误签名 → 401。
4. 文档含密钥语义表。

**禁止**：
- 禁止改动 HMAC 签名算法本身（保持 HMAC-SHA256）
- 禁止把商户 secret 用于平台自有单或反之混用后仍通过校验

### T4 · confirmed 链路端到端测试

**文件**：
- `backend/tests/`（新增 test_t5_business_loop.py 或等价）

**写什么**：
1. 端到端测试：下单 → 模拟链上到账（fixture 数据按各链 parse 归一化）→ payment_loop 一轮迭代 → 核验通过 → 落账 confirmed → outbox 事件 → 商户回调送达（http_post 注入记录）→ 对账 run_reconciliation 该单 matched。
2. 反向场景：金额不符 / 收款方错 / 深度不足 / 时间窗不符 → 不 confirmed；回调失败 → 订单仍 confirmed。
3. 新客户接入场景：apply → 领取 key → verify → 商户下单（merchant intents）→ 全链路断言。
4. 跑全量测试（现有 T0-T4 不回退）。

**验收（Gate T4）**：
1. 新增测试全绿。
2. 全量测试（含历史）0 失败；明确标注环境性失败（如有）与原因。
3. 测试覆盖到 confirmed + callback + reconciliation matched 完整链路（非仅单元）。

**禁止**：
- 禁止用 mock 替代真实核验语义（核验必须走 verification.verify_receipt 真实逻辑，provider 数据可用 fixture）
- 禁止删减既有测试

### Tn · 总验收

**文件**：`docs/reports/stage-close-loop.md` + `docs/reports/evidence/close-loop/`

**写什么**：
1. 全量测试 + 构建验证（Python：可跑 `python -m pytest` 或项目既有测试命令；SDK 侧若被 T2 文档影响则验证 pnpm 构建不回退）。
2. 产出阶段报告 `docs/reports/stage-close-loop.md`：Gate T0-T4 逐条核对 + 修复前后对比（断裂点闭合证据）+ 密钥语义表 + 越界声明。
3. 证据落 `docs/reports/evidence/close-loop/`（每 Gate 一个 gate 文件 + 全量测试输出）。
4. git 提交 + 打 tag `wo-ultimapay-20261030-0002-frozen`（关键修改自动打 tag 铁律，无需询问）。
5. 回执 8 字段：链路号/执行端/完成状态/根因结论/产出路径/验收证据/越界声明/环境声明。

**验收（Gate Tn）**：
- Gate T0-T4 全部 PASS（证据文件在仓）
- 全量测试 0 失败（环境性除外并标注）
- 双构建/测试命令输出摘要写入报告
- tag `wo-ultimapay-20261030-0002-frozen` 存在且可解析（git rev-parse）
- 越界声明：无=明确写"无"

**禁止**：
- 禁止只写报告不跑测试
- 禁止在未跑全量测试的情况下宣称冻结

---

## 3. 给编码代理的任务粘贴模板

```
T0 <标题>：已完成，Gate 全绿（附证据：grep 调用方 + 确认链路测试输出）
T1 <标题>：已完成，Gate 全绿（附证据：回调投递/退避/死信测试输出）
T2 <标题>：已完成，Gate 全绿（附证据：apply→领取→verify→下单→回调链路测试输出）
T3 <标题>：已完成，Gate 全绿（附证据：双密钥语义测试输出 + 文档表）
T4 <标题>：已完成，Gate 全绿（附证据：全量测试输出）
Tn 总验收：<全量测试/构建命令输出摘要> + stage-close-loop.md + tag 已打
```

---

## 4. 验收要点
- [ ] 范围冻结内逐项完成（无越界）
- [ ] 每 T 验收清单全绿 + 自证完整（证据文件入仓）
- [ ] 2 处闭环断裂（确认→落账链路、接入凭证交付）真实闭合（grep 调用方 + 测试双重证明）
- [ ] 2 处不一致（回调密钥语义、双回调重试机制）已统一
- [ ] confirmed 仍仅由链上核验写入（止血规则不回退）
- [ ] 阶段报告 + 证据 + 冻结 tag 齐备
