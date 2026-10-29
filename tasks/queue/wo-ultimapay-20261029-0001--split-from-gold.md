# Codex 工单 · UltimaPay 拆分（从 UltimaGold 拆出为独立产品）

> 版本：v1.0 ｜ 状态：可开工（前置：无）｜ 适用工具：Codex（gpt-5.6-sol · high 主体工程）
> 用法：按 T0→Tn 顺序逐条执行，Gate PASS 后冻结再进下一条。
> 范围依据：用户 2026-10-29 明确要求"UltimaPay 必须从 UltimaGold 拆干净"（业务逻辑/后端/DB 迁移/路由不再捆绑 Gold，Gold 环境不起 Pay 也要能独立运行）；G-DISPATCH-001 自主推进版 + G-TASKFLOW-001。
> **标准依据（必读，禁止跳过）**：
> - 本单派发依据：`H:\UltimaAI\_shared\paradigms\Codex-外部执行端调用范式.md` v1.5（工单三段式 + DoR 五项 + 回执字段 + 打 tag 铁律）
> - 项目引导索引：先读 `H:\UltimaAI\INDEX.md`，按权威源地图定位规范再执行
> - 全系禁词：tuppoau 旧品牌名不得出现在任何新代码/显示层（历史拍板"方案 B"：显示层零残留 + 接口双轨别名 + 表名不动）

---

## 0. UltimaPay 拆分范围冻结

**背景（实测证据）**：UltimaPay 当前**未拆净**——
- `H:\UltimaAI\UltimaGold\ultima-pay\` 只是壳：design/（设计稿）、docs/、reference/（inpayment-sdk 参考）、sdk/（TS 支付窗口 PayWindow + UltimaPaySDK）——**0 个 .py 后端文件**。
- 后端逻辑全部寄生在 UltimaGold：
  1. `H:\UltimaAI\UltimaGold\news-feed\feed_server.py`：`_PAYMENT_CHANNELS` + payment_loop + `/api/tuppoau/payment/*` 全套路由（订单拉起/查询/二维码/链上确认）
  2. `H:\UltimaAI\UltimaGold\backend\ultima_backend\payments\`：`merchant_api.py`（外部商户 UGP-30）、`orders.py`（金额锁死/防撞尾数）、`__init__.py`（通道配置 `_PAYMENT_CHANNELS`）
  3. `H:\UltimaAI\UltimaGold\backend\ultima_backend\db.py`：`_m009_payment_pay_sessions` 支付会话表迁移挂在 Gold 的 DB
- 耦合红线：API 路径 `/api/tuppoau/payment/*`（旧品牌残留）、路由/进程/DB 全依赖 Gold。

**本单只做**：
1. 把上述 3 处后端逻辑整体抽出为独立服务 `H:\UltimaAI\UltimaPay\backend\`（独立 FastAPI/服务入口、独立 DB 或独立 schema、独立进程启动）
2. 路由去 tuppoau：`/api/tuppoau/payment/*` → `/api/pay/*`（对外兼容别名按方案 B 保留双轨或显式迁移）
3. 支付会话表从 Gold 的 DB 迁移到 UltimaPay 独立 DB（m009 迁移随迁）
4. UltimaPay 独立启动验证：**不起 UltimaGold 环境也能独立运行全部支付能力**
5. 新建 `H:\UltimaAI\UltimaPay\docs\INDEX.md`（项目引导索引，按项目引导索引模板）与项目级队列登记（本工单即第一单）
6. 同步：Gold 侧原 payment 逻辑移除后保留调用方指向新服务（或在 Gold 中删除寄生逻辑，由调用方直连 UltimaPay）
7. SDK（`ultima-pay\sdk\`）与接入文档同步更新为独立服务地址/新路由

**本单不做**：
- 不做支付业务规则修改（金额锁死/防撞尾数/通道配置逻辑原样迁移，不重构）
- 不做多链扩展/新通道/新支付方式
- 不改 Gold 其他非支付模块
- 不重写 SDK 支付窗口 UI（仅改指向地址/路由）
- 不部署 VPS、不改 nginx

---

## 1. 任务清单

| 任务 | 文件 | 验收门 |
|---|---|---|
| T0 | `H:\UltimaAI\UltimaPay\backend\` 新建 | 独立服务骨架存在，可 import |
| T1 | 迁移 payments 包（merchant_api/orders/__init__） | 原逻辑全量迁入，零功能缺失 |
| T2 | 迁移 payment_loop + `/api/pay/*` 路由 | 独立路由可启动、可调用 |
| T3 | 迁移 m009 支付会话表到独立 DB | 独立 DB 建表迁移通过 |
| T4 | Gold 侧寄生逻辑移除/转发 + 调用方更新 | Gold 不再承载支付进程逻辑 |
| T5 | SDK/文档同步 + docs/INDEX.md | 指引零 tuppoau 残留，指向独立服务 |
| Tn | 总验收 | 独立启动冒烟全绿 + 回归 + 冻结 tag |

---

## 2. 任务明细（每条 T 严格三段式）

### T0 · 建立 UltimaPay 独立后端骨架
**文件**：
- `H:\UltimaAI\UltimaPay\backend\app\__init__.py`（新建）
- `H:\UltimaAI\UltimaPay\backend\app\main.py`（新建，FastAPI 入口）
- `H:\UltimaAI\UltimaPay\backend\requirements.txt`（新建）
- `H:\UltimaAI\UltimaPay\backend\run.py`（新建，独立启动入口）
**写什么**：
1. 以 `H:\UltimaAI\UltimaGold\backend\ultima_backend\payments\` 现有结构为参照，在 UltimaPay 建独立 backend 包
2. 独立 FastAPI app 实例，应用标题 `UltimaPay API`，独立端口（默认 8061，读环境变量覆盖）
3. requirements 仅含运行支付服务所需依赖（fastapi/uvicorn/httpx/redis/asyncpg 或 sqlite 按 Gold 现用栈迁移）
4. run.py 支持 `python run.py` 单命令启动
**验收（Gate T0）**：
- `H:\UltimaAI\UltimaPay\backend\app\main.py` 存在且可 `python -c "import app.main"` 零报错
- 启动 `python run.py` 后 `http://127.0.0.1:8061/health` 返回 200
**禁止**：
- 禁止新建任何含 tuppoau 的路径/模块名
- 禁止复制 Gold 的非支付业务代码

### T1 · 迁移支付核心包
**文件**：
- 源：`H:\UltimaAI\UltimaGold\backend\ultima_backend\payments\merchant_api.py`、`orders.py`、`__init__.py`
- 目标：`H:\UltimaAI\UltimaPay\backend\app\payments\merchant_api.py`、`orders.py`、`__init__.py`（新建）
**写什么**：
1. 原样迁移 UGP-30 外部商户 API（merchant_api.py）与订单模块（orders.py：金额锁死/防撞尾数），保持业务逻辑逐行等价
2. `_PAYMENT_CHANNELS` 通道配置迁入 `payments\__init__.py`，内容与 Gold 现版一致
3. 内部相对导入改为新包结构（`app.payments.*`）
4. 配置读取改为环境变量/独立配置文件（`H:\UltimaAI\UltimaPay\backend\.env` 或 config.py），不再读 Gold 的配置
**验收（Gate T1）**：
- 三文件迁移后与原文件逐函数 diff 可证明业务逻辑等价（仅改导入与配置读取）
- `python -c "from app.payments import orders"` 零报错
- `_PAYMENT_CHANNELS` 键值与原 Gold 定义完全一致
**禁止**：
- 禁止优化/重构业务逻辑（防撞尾数、金额锁死算法原样）
- 禁止删减任何通道

### T2 · 迁移支付路由与支付循环
**文件**：
- 源：`H:\UltimaAI\UltimaGold\news-feed\feed_server.py`（`_PAYMENT_CHANNELS`、payment_loop、`/api/tuppoau/payment/*` 路由段）
- 目标：`H:\UltimaAI\UltimaPay\backend\app\routes\payment.py`（新建，含 payment_loop）
**写什么**：
1. 把 feed_server.py 中的支付路由全套（订单拉起/查询/二维码/链上确认）迁入新路由模块，**路径改为 `/api/pay/...`**（如 `/api/pay/order/create`、`/api/pay/order/query`、`/api/pay/qrcode`、`/api/pay/onchain-confirm`，按原路由语义一一对应）
2. payment_loop 迁入作为独立后台任务（FastAPI lifespan 或 asyncio task），行为与 Gold 原循环一致（轮询链上确认等）
3. 按方案 B 保留 `/api/tuppoau/payment/*` 兼容别名转发到 `/api/pay/*`（如适用；若 Gold 调用方可一次性迁移则显式声明放弃别名）
**验收（Gate T2）**：
- 独立服务启动后，`/api/pay/*` 全部路由存在（OpenAPI 文档可查）
- payment_loop 随服务启动运行，日志有循环心跳
- 原 feed_server.py 支付路由段已移除（不再注册 tuppoau 支付路由）
**禁止**：
- 禁止保留 tuppoau 路由为唯一入口（别名仅是兼容层）
- 禁止 payment_loop 逻辑行为改变（轮询间隔/确认逻辑与 Gold 一致）

### T3 · 迁移支付会话表到独立 DB
**文件**：
- 源：`H:\UltimaAI\UltimaGold\backend\ultima_backend\db.py`（`_m009_payment_pay_sessions`）
- 目标：`H:\UltimaAI\UltimaPay\backend\app\db.py` + `H:\UltimaAI\UltimaPay\backend\migrations\0009_payment_pay_sessions.py`（新建）
**写什么**：
1. 将 `_m009_payment_pay_sessions` 表定义迁入 UltimaPay 独立 DB 层
2. 独立数据库配置（本地默认 SQLite `H:\UltimaAI\UltimaPay\backend\ultimapay.db`，生产可换 PostgreSQL；连接串走环境变量）
3. 迁移脚本可独立执行建表（与 Gold 的 DB 零依赖）
4. 启动时自动确保表存在（migrate-on-start）
**验收（Gate T3）**：
- 清空/删除 `ultimapay.db` 后冷启动服务，支付会话表自动建出
- Gold 的 db.py 中不再含 `_m009_payment_pay_sessions`（或已标记为废弃并由 UltimaPay 接管）
**禁止**：
- 禁止复用 Gold 数据库文件/连接
- 禁止修改表结构（字段与原 m009 一致）

### T4 · Gold 侧寄生逻辑移除与调用方更新
**文件**：
- `H:\UltimaAI\UltimaGold\news-feed\feed_server.py`
- `H:\UltimaAI\UltimaGold\backend\ultima_backend\payments\`（视拆净程度删除或保留空壳声明）
- `H:\UltimaAI\UltimaGold\backend\ultima_backend\db.py`（m009 段移除）
- Gold 前端/调用方（实际引用 `/api/tuppoau/payment/*` 的页面/客户端代码）
**写什么**：
1. 从 feed_server.py 删除 payment_loop 与支付路由注册（保留或新增一行注释指向 UltimaPay 独立服务地址）
2. 从 Gold db.py 移除 m009 迁移定义（已由 UltimaPay 接管），Gold 启动流程不再执行该迁移
3. 全仓扫描 `tuppoau/payment` 引用：Gold 内所有调用支付接口的代码改为指向 `http://127.0.0.1:8061/api/pay/*`（本地直连规范，见 server-assets §6.4；生产地址留环境变量）
4. `ultima-pay\sdk\` 中指向 `/api/tuppoau/payment/*` 的常量/默认 baseURL 同步改为 `/api/pay/*`
**验收（Gate T4）**：
- `grep -r "tuppoau/payment" H:\UltimaAI\UltimaGold\`（排除 node_modules/备份）→ 仅剩别名兼容层（若有）或 0 命中
- Gold 启动不再拉起支付进程逻辑；UltimaGold 环境关闭时 UltimaPay 服务仍全功能可调用
**禁止**：
- 禁止删除 Gold 其他与支付无关的 feed 业务
- 禁止把 Gold 前端地址写死为 VPS 域名（本地用 127.0.0.1，生产走环境变量）

### T5 · SDK/文档同步 + 项目引导索引
**文件**：
- `H:\UltimaAI\UltimaPay\docs\INDEX.md`（新建，按 `H:\UltimaAI\_shared\templates\项目引导索引模板.md`）
- `H:\UltimaAI\UltimaGold\ultima-pay\README.md` / `docs\`（指向新项目位置，标注"已拆分为独立产品 H:\UltimaAI\UltimaPay"）
- `H:\UltimaAI\UltimaPay\sdk\`（自 ultima-gold\ultima-pay\sdk 迁入，若存在则移动，若无则注明）
**写什么**：
1. 建 UltimaPay 项目引导索引：产品定位、权威源（INDEX.md / 本工单）、目录地图（backend/docs/sdk/tasks）、启动命令、端口、禁词
2. Gold 侧 ultima-pay 目录 README 顶部标注迁移去向，保留旧文档供历史追溯
3. 确认 SDK 迁移/指向：`ultima-pay\sdk` 迁入 `H:\UltimaAI\UltimaPay\sdk`（保留在 Gold 的可加 redirect 注释）
**验收（Gate T5）**：
- `H:\UltimaAI\UltimaPay\docs\INDEX.md` 存在且含启动/端口/权威源信息
- 全仓（UltimaPay + UltimaGold 支付相关文档）`tuppoau` 显示层零残留（文档/UI 层不得出现；接口别名层除外）
- SDK baseURL 指向 `/api/pay/*`
**禁止**：
- 禁止在 UltimaPay 文档中出现 tuppoau 品牌词
- 禁止复制 Gold 全部文档（只建本产品相关）

### Tn · 总验收
**验收（Gate Tn）**：
1. 双启动验证：**UltimaGold 环境不启动** → 仅启动 `H:\UltimaAI\UltimaPay\backend\run.py` → `/health` 200 + `/api/pay/*` 关键路由（订单创建→查询→二维码）真实调用 PASS
2. 回归：迁移后核心逻辑行为与迁移前一致（抽查防撞尾数/金额锁死边界用例）
3. 禁词扫描：UltimaPay 全目录 `tuppoau` 0 命中（别名兼容层除外并注明）
4. 构建验证：`python -m compileall backend` 零错误
5. 越界声明：明确写"无"或列明未动范围
6. 阶段报告：`docs/reports/stage-split-v1.md` 产出，证据入 `docs/reports/evidence/split-v1/`
7. 冻结 tag：`wo-ultimapay-20261029-0001-frozen`（git 可解析）

---

## 3. 给编码代理的任务粘贴模板

```
T0 建立独立后端骨架：已完成，Gate T0 全绿（附 /health 200 证据）
T1 迁移支付核心包：已完成，业务逻辑逐行等价（附 diff 摘要）
T2 迁移支付路由与循环：已完成，/api/pay/* 全路由可查（附 OpenAPI 摘要）
T3 迁移支付会话表：已完成，独立 DB 冷启动建表 PASS
T4 Gold 寄生移除：已完成，tuppoau/payment 扫描 0 命中（附 grep 输出）
T5 SDK/文档同步：已完成，INDEX.md 存在 + 显示层零 tuppoau
总验收：双启动冒烟全绿 + 回归 PASS + compileall 零错误（附命令输出）
```

---

## 4. 验收要点
- [ ] 范围冻结内逐项完成（无越界）
- [ ] 每 T 验收清单全绿 + 自证完整（附命令输出）
- [ ] 关联文档同步（Gold 侧标注迁移去向、UltimaPay INDEX 就绪）
- [ ] 阶段报告产出 `docs/reports/stage-split-v1.md` → 主脑复核 → 冻结 tag
- [ ] 禁词核验：tuppoau 显示层零残留（别名兼容层除外并注明）
