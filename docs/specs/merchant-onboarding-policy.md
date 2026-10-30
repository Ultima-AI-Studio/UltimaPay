# UltimaPay 商户接入准入规范

> 版本：v1.0 ｜ 建立：2026-10-30 ｜ 依据：Owner 明确拍板（2026-10-30：「我们自己的项目，Ultima 开头的，在我们自己 CMS 目录里的，就不需要走正常的 UltimaPay 的接入申请，直接我们就能接」）
> 权威性：本文件为 UltimaPay 商户/项目接入准入的唯一权威判定规则；冲突时以本文件为准，并在 INDEX 引用。

## 1. 准入分类

| 类别 | 判定条件 | 接入路径 | 需要 apply 申请 | 说明 |
|---|---|---|---|---|
| **自有项目（免申请）** | ① 项目名以 `Ultima` 开头；且 ② 已登记在自有 CMS（UltimaAdmin）产品目录下 | **直接接入**：由后台/管理端直接登记商户、下发商户凭据（api_key + secret + callbackUrl），跳过 `/api/pay/apply` 申请与人工审核 | 否 | 自家产品线，属 UltimaPay 显式消费者，接入即开通 |
| **外部商户** | 不满足上述两条（非 Ultima 前缀，或未登记自有 CMS） | 标准申请流程：`/api/pay/apply` → 领取 api_key → `/api/pay/verify` 激活 → 配置收款地址与 callbackUrl → 上线 | 是 | 走正常申请+审核 |

## 2. 判定要点

1. **两条必须同时满足**才可走免申请直连：
   - 项目名以 `Ultima` 开头（如 UltimaAgent / UltimaGold / UltimaConsole / UltimaSentinel / UltimaScan / UltimaRadar / UltimaWallet / UltimaPay 自身）；
   - 已登记在自有 CMS（UltimaAdmin，端口 8913 主控后台）产品目录下。
2. 满足任一条件缺失（如外部项目借用 Ultima 前缀、或 Ultima 项目尚未登记 CMS）→ 回退标准申请流程。
3. 自有项目直连的具体动作由后台/管理端执行，不要求商户侧提交 apply；若商户侧误走 apply，管理端可直接升级为 active 或按自有项目登记处理，不阻塞接入。

## 3. 凭据与开通

- 自有项目直连开通时，管理端生成/下发：`code`（商户代码）、`api_key`、`secret`（回调签名密钥）、`callbackUrl`（回调地址，可选）。
- 密钥轮换、换址工单等规则与外部商户一致（见后端 `rotate_merchant_secret`、`/api/pay/ticket`）。
- 自有项目订单仍走同一支付链路（下单→链上核验→落账→回调），仅准入环节免申请；**不豁免任何支付确认、金额、对账规则**。

## 4. 变更与登记

- 本规范为 Owner 拍板规则；后续调整必须经 Owner 确认并在此登记版本。
- 同步要求：INDEX.md 引用本文件；规则同步至 `H:\UltimaAI\INDEX.md` 相关段落与全局台账（如适用）。
