"""连接预设清单（2026-10-21 批次十五）。

**为什么有它**：用户裁决「如果用户配 opencode go 的套餐，内置就解决了请求头之类的问题」
⇒ 与其让用户手填 ``x-opencode-session``、模型列表地址这些**服务商特有的约定**，
不如把「某服务商需要什么」收进一份清单，用户选一下即可。

**清单的定位**：
- 预设 = **一键填表**（base_url / models_url / headers / provider）；
  填完之后**用户仍可逐项改**（预设不锁字段）⇒ 预设过期或服务商改地址时不会把人卡住。
- 预设**不是**「服务商特判」：代码里**没有**任何 ``if provider == "opencode"`` 的分支
  ⇒ 新增服务商 = 往本清单加一条，**不改任何逻辑**。
- 敏感信息（api_key）**不在预设里** —— 预设只给「连哪里、带什么头」。

⚠️ 权威值在**代码**里（本模块），前端只渲染清单 ⇒ 避免同一组 URL 在前后端各写一份
（那是本仓登记过的「提示词里的阈值与代码常量分叉」同款问题）。
"""

from __future__ import annotations

__all__ = ["CONNECTION_PRESETS", "PRESET_IDS", "get_preset"]


CONNECTION_PRESETS: tuple[dict, ...] = (
    {
        "preset_id": "opencode_go",
        "label": "opencode Go（订阅制）",
        "provider": "openai_compatible",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "params": {
            # opencode 要求每次请求带一个稳定的会话 id（Go 套餐**必需**）
            "headers": {"x-opencode-session": "{{session_id}}"},
            # ⚠️ opencode 的模型列表**不在** {base_url}/models 上
            "models_url": "https://opencode.ai/inference/go/v1/models",
        },
        "note": (
            "填好 API 密钥（oc_sk_…）即可用；会话 id 由 NovelOS 按写作 run 自动派生，"
            "无需自己填"
        ),
    },
    {
        "preset_id": "opencode_payg",
        "label": "opencode 按量付费",
        "provider": "openai_compatible",
        "base_url": "https://opencode.ai/inference/openai/v1",
        "params": {
            "headers": {"x-opencode-session": "{{session_id}}"},
            "models_url": "https://opencode.ai/inference/v1/models",
        },
        "note": "同 opencode Go，仅计费方式不同（按量付费）",
    },
    {
        "preset_id": "opencode_go_anthropic",
        "label": "opencode Go（Anthropic 兼容协议）",
        "provider": "anthropic",
        # 官方基础 URL https://opencode.ai/inference/go + /anthropic/v1/messages
        # ⇒ NovelOS 的 AnthropicProvider 会拼 /v1/messages ⇒ base_url 到 /anthropic 为止
        "base_url": "https://opencode.ai/inference/go/anthropic",
        "params": {
            "headers": {"x-opencode-session": "{{session_id}}"},
        },
        "note": (
            "Claude 系模型走 Anthropic 原生协议（prompt cache 支持更好）；"
            "该端点不提供模型列表，请手工填写模型名"
        ),
    },
    {
        "preset_id": "commandcode_goat",
        "label": "Command Code GOAT（$10/月，$70 额度）",
        "provider": "openai_compatible",
        "base_url": "https://api.commandcode.ai/provider/v1",
        "params": {
            # ⚠️ **GOAT 套餐官方要求每个请求带 x-cmd-zdr: 1**（缺了会被拒）——
            #    这是本清单里**除 opencode 外**唯一需要固定请求头的服务商。
            "headers": {"x-cmd-zdr": "1"},
            "models_url": "https://api.commandcode.ai/provider/v1/models",
        },
        "note": (
            "密钥形如 user_…（不是 sk-…）；模型 id 带厂商前缀"
            "（如 deepseek/deepseek-v4-flash、z-ai/glm-5.3-flash）"
        ),
    },
    {
        "preset_id": "kimi_code",
        "label": "Kimi Code（订阅制）",
        "provider": "openai_compatible",
        "base_url": "https://api.kimi.com/coding/v1",
        "params": {
            "models_url": "https://api.kimi.com/coding/v1/models",
        },
        "note": (
            "Kimi Code 密钥（sk-kimi-…）**只适用于** api.kimi.com/coding，"
            "发到 api.moonshot.cn 会 401；模型如 k3 / kimi-for-coding"
        ),
    },
    {
        "preset_id": "minimax_cn",
        "label": "MiniMax（国内）",
        "provider": "openai_compatible",
        "base_url": "https://api.minimax.cn/v1",
        "params": {},
        "note": "本仓 dev 库实测地址；模型如 MiniMax-M3 / MiniMax-M2.7",
    },
    {
        "preset_id": "opendesign_go",
        "label": "OpenDesign Go（$10/月，$120 额度）",
        "provider": "openai_compatible",
        # ⚠️ OpenDesign 官方未公开固定的 inference 端点 ⇒ **留空**，由用户从
        #    账号页（API Key 旁）取实际地址填入。**不编造**。
        "base_url": None,
        "params": {},
        "note": (
            "OpenDesign 的 API 地址官方未公开固定值，请从账号页 API Key 旁复制填入"
            "（Go 套餐含 DeepSeek V4 / GLM-5.2 / Kimi K2.7 Code / MiniMax M2.7 等）"
        ),
    },
    {
        "preset_id": "openai",
        "label": "OpenAI 官方",
        "provider": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "params": {},
        "note": None,
    },
    {
        "preset_id": "anthropic",
        "label": "Anthropic 官方（Claude 原生 Messages API）",
        "provider": "anthropic",
        "base_url": None,
        "params": {},
        "note": "接口地址留空即用官方默认",
    },
    {
        "preset_id": "deepseek",
        "label": "DeepSeek",
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "params": {},
        "note": None,
    },
    {
        "preset_id": "kimi",
        "label": "Kimi / Moonshot",
        "provider": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "params": {},
        "note": None,
    },
    {
        "preset_id": "ollama_local",
        "label": "Ollama（本地，无需密钥）",
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "params": {},
        "note": "本地模型服务；模型名用 Ollama 自己的 /api/tags 列表",
    },
)
"""连接预设清单（**只读**）。

每个预设的键：
- ``preset_id`` —— 稳定标识；
- ``label`` —— 前端显示名；
- ``provider`` / ``base_url`` —— 一键填的接口类型与地址（``base_url`` 可为 ``None``）；
- ``params`` —— 一键填的连接级参数（``headers`` / ``models_url``；**不含** api_key）；
- ``note`` —— 给用户看的一句提示（可为 ``None``）。
"""

PRESET_IDS: frozenset[str] = frozenset(p["preset_id"] for p in CONNECTION_PRESETS)
"""全部预设 id（供写时校验用）。"""


def get_preset(preset_id: str | None) -> dict | None:
    """按 id 取一个预设；未知 / 空 → ``None``。"""
    if not preset_id:
        return None
    for p in CONNECTION_PRESETS:
        if p["preset_id"] == preset_id:
            return dict(p)
    return None
