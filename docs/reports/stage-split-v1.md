# UltimaPay 拆分阶段报告 v1

> 工单：`wo-ultimapay-20261029-0001`
> 状态：执行中
> 独立计划评审：PASS
> 计划 SHA-256：`5D146711E74F669F8BF05A61C77B6B038ADB23434EE110E679A3C4E73F9F558A`

## 范围裁决

1. 迁移完整支付包及链适配器；工单摘要漏列的直接依赖不可删减。
2. 旧产品数据标识仅允许存在于明确标注的兼容常量模块；新代码和显示层不重复该词。
3. UltimaGold 的履约与邀请消费者属于非支付业务，保留为独立权益循环；支付轮询迁入 UltimaPay。
4. UltimaGold 无 Git 仓库，UltimaPay tag 只冻结 UltimaPay 产出；Gold 改动另存前后哈希及正反补丁。

## Gate 记录

| Gate | 状态 | 证据 |
|---|---|---|
| T0 | PASS | `evidence/split-v1/t0-gate.txt`：独立环境依赖导入通过；pytest 1/1；Gold 8912 未启动时真实 8061 `/health`=200 |
| T1 | 未开始 | — |
| T2 | 未开始 | — |
| T3 | 未开始 | — |
| T4 | 未开始 | — |
| T5 | 未开始 | — |
| Tn | 未开始 | — |

