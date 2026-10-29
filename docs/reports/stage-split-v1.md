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
| T1 | PASS | `evidence/split-v1/t1-gate.txt`：完整 14 文件迁移；AST 函数签名等价；pytest 3/3；冷库基础表 10/10；禁词仅兼容常量层 1 处 |
| T2 | PASS | `evidence/split-v1/t2-gate.txt`：pytest 8/8；OpenAPI 17 条 `/api/pay/*`；Gold 停机时真实冷启动/订单/查询/二维码/鉴权全绿；循环心跳可见；Gold 原支付路由与循环零命中 |
| T3 | PASS | `evidence/split-v1/t3-gate.txt`：pytest 11/11；独立冷库首次迁移 `[9]`、重跑 `[]`；m009 字段/索引原样；会话单订单隔离 200/403；Gold m009 所有权零残留 |
| T4 | PASS | `evidence/split-v1/t4-gate.txt`：Gold 支付包/路由/循环/会话迁移所有权已移除；正反补丁双向校验 0；边界测试 4/4、非支付回归 6/6；Gold 8912 停止时独立真实 HTTP 冒烟全绿 |
| T5 | PASS | `evidence/split-v1/t5-gate.txt`：项目 INDEX/README 就绪；SDK 完整迁入并改为 8061 + `/api/pay/*`；Python 契约 3/3、SDK Vitest 10/10、PayWindow 与根 SDK 构建通过；Gold 仅留迁移指向 |
| Tn | 未开始 | — |

