# Changelog

所有重要的版本更新都会在这个文件中记录。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/) 语义化版本规范。

## [1.0.0] - 2026-10-15

### 新增

- Ultima Pay SDK 首个独立版本，由 inpayment-sdk v1.1.8 完整迁移
- 保留全部链上能力：ETH / ERC20 代币购买、锁仓释放（线性 / 阶梯 / 不锁仓）、推荐返佣、价格预言机、项目销售进度

### 修复

- 修复 `getUnlockTime` 默认释放逻辑：项目方未开启自动释放且未手动设置释放时间时，不再返回错误的起始时间，按文档口径回退到「预售结束时间 + 30 天」起算
- 修复 `getVestingType` 空指针：未调用 `init()` 直接调用时自动补初始化，不再抛 TypeError
- 清理根目录误提交的 0 字节空文件 `inpayment-sdk@1.0.0`
- 清理指向旧属主 ArkExlabs 的 repository / bugs / homepage 元数据
- 移除未使用的 dayjs 遗留依赖；ethers v6 由 devDependencies 移入 dependencies（运行时必需）

### 变更

- 品牌化：包名 `inpayment-sdk` → `ultima-pay-sdk`；主类 `InpaymentSDK` → `UltimaPaySDK`；选项接口 `InpaymentSDKOptions` → `UltimaPaySDKOptions`
- 版本从 1.1.8 重置为 1.0.0（Ultima Pay 产品线独立起点）
- 移除指向 inpayment 配套服务的开放 API 文档段落（Ultima Pay 服务端另行规划）

---

## 迁移来源历史（inpayment-sdk，供追溯）

以下为迁移前 inpayment-sdk 的版本记录，仅作来源追溯：

## [1.1.8] - 2025-06-14

### 优化

- 优化了代币锁仓释放逻辑的计算方式，修复解锁时间方法错误问题

## [1.1.7] - 2025-06-14

### 文档

- 更新了 README.md，补充了开放API使用

## [1.1.6] - 2024-04-20

### 优化

- 优化了代币锁仓释放逻辑的计算方式，现在支持三种释放模式

## [1.1.0] - 2024-04-19

### 新增

- 初始版本发布：项目信息查询、ETH 购买代币、ERC20 代币购买、代币释放、锁仓计划管理
