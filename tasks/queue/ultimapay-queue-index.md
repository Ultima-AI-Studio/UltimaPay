# UltimaPay 队列主表（项目级唯一权威）

> 建立：2026-10-29 ｜ 依据：G-DISPATCH-001 自主推进版 + Codex 外部执行端调用范式 v1.5
> 唯一权威：本表为 UltimaPay 项目 Codex 队列的项目级唯一权威；全局聚合见 `H:\UltimaAI\_shared\queue\global-queue-index.md`。
> 状态机：待派发 → 执行中 → 审计中 → 已冻结；一单一进程；每项目执行中 ≤1。

## 一、队列总览

| 工单数 | 待派发 | 执行中 | 审计中 | 阻塞 | 已冻结 |
|---|---|---|---|---|---|
| 2 | 0 | 0 | 0 | 0 | 2 |

## 二、工单明细

| 工单 | 标题 | 状态 | 前置冻结 | 验收门 | 依据 |
|---|---|---|---|---|---|
| wo-ultimapay-20261029-0001 | UltimaPay｜重构｜从 UltimaGold 拆出为独立支付产品 | 已冻结 | 无 | stage-split-v1.md + wo-ultimapay-20261029-0001-frozen | T0–Tn 全绿；独立审计 4 项重要发现均已修复；用户要求的支付逻辑/后端/DB/路由与 Gold 解耦完成 |
| wo-ultimapay-20261030-0002 | UltimaPay｜修复｜业务闭环打通（链上确认→落账→履约通知 + 接入凭证交付） | 已冻结 | wo-ultimapay-20261029-0001-frozen | stage-close-loop.md + wo-ultimapay-20261030-0002-frozen | T0-T4 与 Tn 本地全绿；独立审查 7 项 Important 全部 RED→GREEN 修复；后端 45/45、SDK 10/10、双构建通过 |

## 三、回执与证据

拆分回执见 `docs/reports/stage-split-v1.md`；业务闭环回执见 `docs/reports/stage-close-loop.md`；最终证据见 `docs/reports/evidence/close-loop/tn-gate.txt`。
