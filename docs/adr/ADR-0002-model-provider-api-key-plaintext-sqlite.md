# ADR-0002：model provider API Key 在本地 SQLite 明文存储（MVP 决策）

> 状态：**Accepted**
> 日期：2026-08-24
> 生效：MVP
> 关联：`docs/impl/IMPLEMENTATION-PLAN-v0.md`（历史计划）§4.2 deviation #4；`packages/core/api/routers/model_configs.py`
> 起草：NovelOS 主控

---

## 1. 背景

NovelOS 是本地优先、单用户使用的 FastAPI + React + SQLite 小说写作 OS。MVP 阶段没有用户认证体系，也没有面向局域网或云端的多用户同步需求。模型服务商 API Key 需要在本地调用模型时可用，但产品当前以单机本地使用为边界。

## 2. 决策

MVP 阶段将 model provider API Key 以明文形式保存在本地 SQLite `model_configs.params_json` 中，与模型配置、调用参数和本地用户数据共同管理。

API 返回、日志、备份等对外或持久化导出路径必须继续执行敏感信息防护，不因存储决策而扩大泄露面。

## 3. 理由

- **本地优先**是 MVP 的核心产品形态；单用户本机场景下，密钥的主要风险来自本机文件被直接读取，而不是跨用户访问。
- 若引入数据库字段加密，密钥仍需以可解密形式存放于本机；DPAPI、keyring 等系统级存储并不会消除本机失陷风险，且会增加迁移、恢复和跨平台复杂度。
- 与 LM Studio、SillyTavern 等本地工具的社区惯例一致：配置通常保存在本地文件中，应用负责在需要时读取调用。
- 当前目标是快速交付可用的本地写作工作流，MVP 的收益集中在简化配置、恢复和离线可用性；该取舍是明确的阶段性决策，而非永久安全边界。

## 4. 既有防线

- `packages/core/api/routers/model_configs.py` 的 router 层 `_mask_response` 对 list/get/create/patch 全部出口掩码 `api_key`，并返回 `has_api_key` 标志。
- `ai_call_logs` 对敏感字段设置黑名单，调用日志不把 API Key 作为可追踪日志内容。
- backup 导出采用白名单并排除 `model_configs`，同时在 metadata 中标记 `api_keys_stripped`。
- 日志路径不打印 API Key；任何新增日志或诊断输出都应遵守同一原则。

### 4.1 增补（2026-10-21 批次十五「连接层」）

引入「连接层」（`model_connections`）后，**同一决策的适用面扩大了**：
`model_connections.params_json.api_key` 与 `model_profiles.params_json.api_key`
一样是**明文存 SQLite**（`model_profiles` 那一列自 0016 迁移起就是这样，
本 ADR 原先只提 `model_configs` ⇒ 防线清单与现实脱节，此处补齐）。

新增/强化的防线：

- **读路径掩码扩到连接表**：`api/routers/connections.py::_mask_connection` 对
  list/get/create/patch 全部出口掩 `api_key` 并返回 `has_api_key`；
  且 `headers` 里的**敏感头逐键掩码**（`authorization` / `x-api-key` / `api-key` /
  `x-goog-api-key` / `proxy-authorization` / `cookie` / `set-cookie` / `x-auth-token`），
  非凭据头（如 `x-opencode-session`）**不掩**。
- **backup 白名单同样排除 `model_connections` / `model_profiles`**（白名单是闭合的，
  新表默认不在内）——⚠️ **这条必须有断言**（`tests/api/test_model_connections_api.py::
  test_model_connections_not_in_export_whitelist`），不能靠「白名单默认不包含」这个推理。
- **写路径的键语义分层**：`headers` 是 dict ⇒ 走**逐键** PATCH 语义
  （`***` 保留该键原值 / `""`·`None` 删该键 / 缺键保留），
  **不能**套用标量 `api_key` 的三态 ⇒ 否则前端把读回的 `***` 原样提交会把
  **真值写成 `***`**（密钥静默失效）。
- **鉴权头与 api_key 撞车 ⇒ 写时 422**：连接既配 `api_key` 又在 `headers` 里写
  鉴权头 ⇒ 拒绝（二选一）——否则自定义头会**静默架空**密钥通道，而
  `has_api_key` 仍显示 true（「显示与实际失真」）。
- **日志路径仍不打印 API Key**：新增的 `model_router.fallback.*` warning 只带
  `capability` / `config_id` / 错误串，不带 params。

## 5. 风险与升级路径

### 5.1 风险

- 本机数据库文件被直接读取、误打包、误备份或被恶意进程扫描时，API Key 存在泄露风险。
- 明文配置会随 SQLite 文件整体复制；当前 backup 白名单降低了导出风险，但无法替代本机文件权限和访问控制。
- 一旦产品增加远程访问、局域网服务、多用户认证或云同步，单机边界假设将失效，明文存储的暴露面会显著扩大。

### 5.2 升级路径

未来若支持局域网/云同步、远程 daemon 或多用户认证，应在配置边界引入操作系统级密钥保管，例如 Windows DPAPI、macOS Keychain 或 Linux keyring，并设计密钥标识与解密依赖的版本化迁移。届时还应补充密钥轮换、访问审计、备份重新加密和跨设备同步策略。

## 6. Alternatives Considered

### 6.1 数据库字段加密

- 优点：数据库文件脱离应用环境后更难直接读取。
- 缺点：密钥仍需在本机可解密存放；需要引入密钥管理、迁移与恢复流程，MVP 复杂度高、收益有限。
- 取舍：MVP 暂不采用，未来随远程或多用户能力升级。

### 6.2 仅使用环境变量

- 优点：避免将 Key 写入数据库。
- 缺点：每个 provider 的配置、配置界面和本地恢复体验割裂；无法支持用户通过 model_configs 管理 provider 参数。
- 取舍：环境变量作为兼容入口保留，但完整的本地配置仍允许 `params_json.api_key`。
