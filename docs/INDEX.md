# UltimaPay 项目引导索引（INDEX）

> **本文件是 UltimaPay 的执行入口**：任何开发、审计或同步动作前，先读本文件定位权威源，再读对应范式，最后执行工单。
> 版本：2026-10-29 ｜ 制式：Ultima AI 项目引导索引

## 0. 三条铁律

1. 先读 `H:\UltimaAI\INDEX.md`，再读全局任务工作流与执行端范式，最后执行本项目工单。
2. UltimaPay 必须保持独立进程、独立数据库和独立路由；UltimaGold 未启动时支付能力仍应完整可用。
3. 新代码、SDK 和显示文档不得出现已退役品牌词；仅 `backend\app\compat.py` 可保存明确标注的数据兼容常量。

## 1. 权威源地图

| 内容 | 权威路径 |
|---|---|
| 根级导航 | `H:\UltimaAI\INDEX.md` |
| 顶层架构标准 | `H:\UltimaAI\ULTIMA-STANDARD.md` |
| 任务工作流 | `H:\UltimaAI\_shared\paradigms\UltimaAI-任务工作流范式.md` |
| Codex 执行规范 | `H:\UltimaAI\_shared\paradigms\Codex-外部执行端调用范式.md` |
| 商户接入准入 | `docs\specs\merchant-onboarding-policy.md`（自有 Ultima 项目免 apply 直连；外部商户走标准申请） |
| 本工单 | `tasks\queue\wo-ultimapay-20261029-0001--split-from-gold.md` |
| 项目队列 | `tasks\queue\ultimapay-queue-index.md` |
| 阶段报告 | `docs\reports\stage-split-v1.md` |
| Gate 证据 | `docs\reports\evidence\split-v1\` |

## 2. 目录地图

| 目录 | 职责 |
|---|---|
| `backend\` | FastAPI 独立服务、支付核心、迁移、测试与启动入口 |
| `sdk\` | 独立服务 SDK 与 PayWindow 组件 |
| `docs\` | 项目索引、阶段报告与正式证据 |
| `tasks\queue\` | 项目队列与原工单 |

## 3. 独立启动与接口

```powershell
cd H:\UltimaAI\UltimaPay\backend
python run.py
```

- 默认监听：`http://127.0.0.1:8061`
- 健康检查：`GET /health`
- 规范支付接口：`/api/pay/*`
- 默认数据库：`H:\UltimaAI\UltimaPay\backend\ultimapay.db`
- 环境覆盖：`ULTIMAPAY_HOST`、`ULTIMAPAY_PORT`、`ULTIMAPAY_DATABASE_URL`、`ULTIMAPAY_USER_AUTH_SECRET`、`ULTIMAPAY_ADMIN_TOKEN`

## 4. 验收命令

```powershell
cd H:\UltimaAI\UltimaPay\backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app migrations run.py
```

每个 Gate 必须先通过、写入报告和证据目录并创建对应 tag，才可进入下一阶段；工单最终冻结 tag 为 `wo-ultimapay-20261029-0001-frozen`。

## 5. 边界

- 不修改金额锁死、防撞尾数、支付确认状态机或通道业务规则。
- 不把 UltimaGold 的非支付模块迁入本项目。
- 本地服务只使用 `127.0.0.1:8061`；生产地址通过环境配置，不在 SDK 或文档写死公网域名。
