# 模型连接层使用手册（连接与档案解耦）

> 面向使用者的操作说明。实现细节见 `packages/core/model_router/README.md`；
> 设计决策与踩坑记录见 `AGENTS.md` 坑区、`CHANGELOG.md` 批次十五。

## 一、为什么有这一层

以前一个「模型档案」把**连到哪里**（provider / base_url / api_key）和**用什么模型**（model）
焊在一起。于是同一厂商想让不同模型承担不同职责时，就得建好几份档案，
把 base_url、api_key 各重填一遍 —— 改一次 key 要改好几处，漏一处就串味。

现在拆成两层：

| 概念 | 是什么 | 在哪配 |
|---|---|---|
| **连接**（connection） | 连到哪里、怎么认证、带哪些请求头 | 「AI 设置 → 模型连接」面板 |
| **档案**（profile） | 用哪个模型、怎么调（思考模式 / 温度 / 超时…） | 「AI 设置 → 模型档案」面板 |

**同一厂商的多个档案可以共享一条连接** —— 改一次 key / base_url / 请求头，全部同时生效。

## 二、日常用法

### 1. 建一条连接

「AI 设置 → 模型连接 → 新建」：

| 字段 | 说明 |
|---|---|
| **厂商模板**（预设） | **先选这个** —— 选完自动填好接口地址 / 请求头 / 模型列表地址，你只填密钥即可。填完仍可逐项改 |
| 名称 | 显示名，随便起（如 `opencode`） |
| 接口类型 | `openai_compatible` / `anthropic` / `ollama` / `mock` |
| 接口地址 | `openai_compatible` **必填**（没有默认端点）；anthropic / ollama 留空用官方默认 |
| API 密钥 | 留空表示「不设置」；⚠️ 输过一次之后框里显示 `***`（不回填明文），**留空保存 = 不改** |
| 自定义请求头 | 键值对，可增删行 |
| 模型列表地址 | 留空 = 默认 `{接口地址}/models`；**有些服务的模型列表不在这个路径上**，需要单独填 |

#### 现成的厂商模板

| 模板 | 接口地址 | 自带请求头 | 说明 |
|---|---|---|---|
| **opencode Go**（订阅） | `https://opencode.ai/inference/go/openai/v1` | `x-opencode-session` ✅ | 密钥 `oc_sk_…`；会话 id 自动派生，**不用你填** |
| opencode Go（Anthropic 协议） | `https://opencode.ai/inference/go/anthropic` | `x-opencode-session` ✅ | Claude 系模型走原生协议（prompt cache 更好）；模型名要手填 |
| opencode 按量付费 | `https://opencode.ai/inference/openai/v1` | `x-opencode-session` ✅ | |
| **Command Code GOAT**（$10/月） | `https://api.commandcode.ai/provider/v1` | `x-cmd-zdr: 1` ✅ | ⚠️ **GOAT 套餐官方要求每个请求带这个头**；密钥形如 `user_…`（不是 `sk-…`）；模型 id 带厂商前缀（`deepseek/deepseek-v4-flash`） |
| **Kimi Code**（订阅） | `https://api.kimi.com/coding/v1` | — | ⚠️ Kimi Code 密钥（`sk-kimi-…`）**只适用于** `api.kimi.com/coding`，发到 `api.moonshot.cn` 会 401 |
| **MiniMax**（国内） | `https://api.minimax.cn/v1` | — | 模型如 `MiniMax-M3` / `MiniMax-M2.7` |
| **OpenDesign Go**（$10/月） | **留空**（官方未公开固定地址） | — | 请从账号页 API Key 旁复制地址填入；含 DeepSeek V4 / GLM-5.2 / Kimi K2.7 Code / MiniMax M2.7 等 |
| OpenAI 官方 | `https://api.openai.com/v1` | — | |
| Anthropic 官方 | 留空（用官方默认） | — | |
| DeepSeek | `https://api.deepseek.com/v1` | — | |
| Kimi / Moonshot 平台 | `https://api.moonshot.cn/v1` | — | ⚠️ 与上面的 Kimi Code **不是同一个产品** |
| Ollama（本地） | `http://127.0.0.1:11434` | — | 无需密钥 |

> 模板清单在后端 `packages/core/model_router/presets.py`（**唯一权威**），
> 前端只渲染、不硬编码任何地址 ⇒ 服务商改地址时改一处即可。

### 2. 在连接上挂模型

在连接那一行点 **「+ 模型」** —— **只需要填模型名**（base_url / key 都在连接上，不用重填）。
挂好后这条档案就可以在「设为默认 ▾」里绑到具体环节（正文写作 / 推理规划 / 轻量评审 …）。

### 3. 存量档案归组（可选）

老档案是在「连接层」出现之前建的，各自带着自己的 base_url / api_key。
想让它们也享受「改一处全局生效」，走一次归组。

> ⚠️ **2026-10-21 实测：归组目前只有 API，没有 UI 入口**（原手册写的「档案面板 →
> 「按连接归组」按钮」**尚未实现**，此处订正，避免照着找不到）。两条 API：
>
> | 步骤 | 端点 | 作用 |
> |---|---|---|
> | ① 预览 | `GET /api/model-profiles/group-by-connection` | 按「生效密钥 + 地址」分组返回，附 `key_fingerprint` 与 `key_consistent` |
> | ② 执行 | `POST /api/model-profiles/apply-grouping` | 逐条校验后写入 `connection_id`；生效密钥与目标连接不一致 ⇒ **422 拒绝**（不静默换密钥） |
>
> 归组后档案不再用自己的密钥，所以密钥不一致时必须拒绝 ——
> 否则会造成「某个档案突然 401」或「计费到别人的号」。

分组用的是**生效密钥**而不是档案里写着的那个 —— 因为密钥还可能来自 `secrets.json`
的档案级条目，按写着的那个分组会把两个档案合并，造成**凭据串用**。

## 三、opencode 接入

opencode 的 inference 端点要求**每次请求都带一个稳定的 `x-opencode-session` 头**
（用于把一段对话稳定路由、复用 prompt cache）。它的模型列表**也不在** `{base_url}/models` 上。

### Go 套餐（订阅制）

建连接时填：

| 字段 | 值 |
|---|---|
| 名称 | `opencode` |
| 接口类型 | `openai_compatible` |
| 接口地址 | `https://opencode.ai/inference/go/openai/v1` |
| API 密钥 | 你的 `oc_sk_...`（走 `Authorization: Bearer`，与其它 OpenAI 兼容服务一致） |
| 自定义请求头 | `x-opencode-session` = `{{session_id}}` |
| 模型列表地址 | `https://opencode.ai/inference/go/v1/models` |

然后在连接上点「+ 模型」，模型名填 opencode 侧的实际 model id
（文档里给的是展示名，如 `Claude Opus 5.5` / `GPT 5.5` / `GLM 5.3` / `Kimi K2.7` / `MiniMax M3`），
**以「拉取模型」列出的 id 为准**。

### 会话头怎么来的

`{{session_id}}` 是个占位符，运行时由 NovelOS 填：

- **写作 run 内** ⇒ `ses_<sha256(run_id)[:32]>` —— **每个写作 run 一个**，
  同一 run 里的写作 / 观察 / 评审共享同一个会话 id（跨 run 不串味）；
- **探针路径**（「测试连接」这类手工操作，没有 run 上下文）⇒ `ses_probe_<sha256(连接 id)[:16]>`，
  按连接派生且恒定。

⚠️ 写 `{{session_id}}` **就行**，前缀 `ses_` 会自动加 —— 不要再写成 `ses_{{session_id}}`
（那会被拒绝，提示前缀重复）。

可用的占位符只有两个：`{{run_id}}`（原始 run id）与 `{{session_id}}`。
写别的会在保存时被拒（避免把字面量 `{{xxx}}` 发给上游）。

### 按量付费（pay-as-you-go）

把接口地址换成 `https://opencode.ai/inference/openai/v1`、
模型列表地址换成 `https://opencode.ai/inference/v1/models` 即可，其余同。

## 四、常见问题

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 「测试连接」失败，但实际能写字 | 模型列表端点路径不对（很多服务不实现 `{base_url}/models`） | 在连接里填「模型列表地址」；或直接在档案上手填模型名 |
| 档案显示「已配置密钥」，但调用 401 | 档案绑着连接，密钥在**连接**上 | 去连接面板看密钥状态 |
| 改了连接的 key，某个档案还是 401 | 该档案是「独立配置」（没绑连接） | 用「按连接归组」把它挂到连接上 |
| 想让一个档案回到自己填地址 | 编辑档案 → 解除连接关联 | 之后它变回独立配置，base_url / api_key 回到档案自己的字段 |
| 删连接被拒 | 有档案还引用着 | 先改那些档案的归属，再删 |
