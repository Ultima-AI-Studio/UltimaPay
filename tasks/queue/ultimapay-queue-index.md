# UltimaPay 队列主表（项目级唯一权威）

> 建立：2026-10-29 ｜ 依据：G-DISPATCH-001 自主推进版 + Codex 外部执行端调用范式 v1.5
> 唯一权威：本表为 UltimaPay 项目 Codex 队列的项目级唯一权威；全局聚合见 `H:\UltimaAI\_shared\queue\global-queue-index.md`。
> 状态机：待派发 → 执行中 → 审计中 → 已冻结；一单一进程；每项目执行中 ≤1。

## 一、队列总览

| 工单数 | 待派发 | 执行中 | 审计中 | 阻塞 | 已冻结 |
|---|---|---|---|---|---|
| 1 | 0 | 0 | 0 | 0 | 1 |

## 二、工单明细

| 工单 | 标题 | 状态 | 前置冻结 | 验收门 | 依据 |
|---|---|---|---|---|---|
| wo-ultimapay-20261029-0001 | UltimaPay｜重构｜从 UltimaGold 拆出为独立支付产品 | 已冻结 | 无 | stage-split-v1.md + wo-ultimapay-20261029-0001-frozen | T0–Tn 全绿；独立审计 4 项重要发现均已修复；用户要求的支付逻辑/后端/DB/路由与 Gold 解耦完成 |

## 三、回执与证据

标准化 8 字段回执见 `docs/reports/stage-split-v1.md`；审计见 `docs/reports/split-v1-audit.md`；最终证据见 `docs/reports/evidence/split-v1/tn-gate.txt`。
