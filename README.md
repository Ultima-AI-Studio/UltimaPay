# UltimaPay

UltimaPay 是从 UltimaGold 完整拆出的独立支付产品：独立进程、独立数据库、独立 `/api/pay/*` 接口，不依赖 UltimaGold 启动。

## 本地启动

```powershell
cd H:\UltimaAI\UltimaPay\backend
.\.venv\Scripts\python.exe run.py
```

- 服务地址：`http://127.0.0.1:8061`
- 健康检查：`http://127.0.0.1:8061/health`
- 规范接口：`http://127.0.0.1:8061/api/pay/*`
- 默认数据库：`backend\ultimapay.db`（可用 `ULTIMAPAY_DATABASE_URL` 覆盖）

执行入口、权威源和目录边界见 [`docs/INDEX.md`](docs/INDEX.md)。
