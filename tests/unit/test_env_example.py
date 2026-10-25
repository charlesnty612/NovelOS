"""``.env.example`` 模板看守（BUG-39 · OS开发B · 2026-10-25）。

为什么需要它
------------
``BUG-39``：仓内 ``.py`` 真实读取的 ``NOVELOS_*`` 键此前**没有模板** ⇒ 新克隆者
无从得知「有哪些必填项」，也不知道**哪些键是凭据**（该给空占位，而不是随手写值）。

本文件只钉三件事（**只钉「键是否存在／值是否为空」，不钉任何默认值与默认路径**）：

1. 模板必须存在，且必须含**全部 39 个生效键**（7 核心 ＋ 26 生产可配 ＋ 6 凭据）；
2. **6 个凭据键只给空占位**（值一律为空 ⇒ 模板里不可能藏着真实 token）；
3. ``NOVELOS_ALLOW_PUSH`` **只以注释出现**（危险开关，默认不设）、``NOVELOS_CLI``
   是纯文档键 **不入模板**。

★ **守卫式写法**：先断言模板文件存在、再解析 ⇒ 模板缺失时是 **FAILED**，
而不是 ``FileNotFoundError``（ERROR）⇒ 红段判据不被误读成「缺功能」。

⚠️ **不与 B1 耦合**：本文件**不**断言任何键的默认落点（``runner.py`` 的调试目录
默认落在哪里由那批用例管）⇒ 两批互不牵动。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / ".env.example"

#: 核心键（7）——数据根／库路径／凭据文件／日志／端口。
CORE_KEYS: tuple[str, ...] = (
    "NOVELOS_API_HOST",
    "NOVELOS_API_PORT",
    "NOVELOS_DATA_DIR",
    "NOVELOS_DB_PATH",
    "NOVELOS_LOG_LEVEL",
    "NOVELOS_PORT",
    "NOVELOS_SECRETS_FILE",
)

#: 凭据键（6）——**只允许空占位**。
CREDENTIAL_KEYS: tuple[str, ...] = (
    "NOVELOS_API_KEY_ANTHROPIC",
    "NOVELOS_API_KEY_DEEPSEEK",
    "NOVELOS_API_KEY_KIMI",
    "NOVELOS_API_KEY_OPENAI",
    "NOVELOS_API_KEY_OPENAI_COMPATIBLE",
    "NOVELOS_BACKUP_KEY",
)

#: 生产可配键（26）。
OPTIONAL_KEYS: tuple[str, ...] = (
    "NOVELOS_ALLOW_REPO_LOCAL_DB",
    "NOVELOS_ARTIFACTS_DIR",
    "NOVELOS_AUTO_REVISE_MAX",
    "NOVELOS_BACKUP_DIR",
    "NOVELOS_BASE_URL",
    "NOVELOS_CONTENT_DIR",
    "NOVELOS_CONTEXT_RELEVANCE",
    "NOVELOS_CRITIC_MODE",
    "NOVELOS_DEBUG_DIR",
    "NOVELOS_DEBUG_OBSERVER_PAYLOAD",
    "NOVELOS_DEBUG_PROVIDER_DUMP_DIR",
    "NOVELOS_DISABLED_MODULES",
    "NOVELOS_FAST_MODE",
    "NOVELOS_INSTANCE_ID",
    "NOVELOS_OBSERVER_PARALLEL",
    "NOVELOS_OBSERVER_SPLIT",
    "NOVELOS_PERF_CHILD",
    "NOVELOS_PROMPT_SYNC",
    "NOVELOS_QUALITY_GATE",
    "NOVELOS_QUALITY_Q8_STRICT",
    "NOVELOS_REPO_ROOT",
    "NOVELOS_REQUIRE_HUMAN_BASELINE",
    "NOVELOS_SUMMARY_PARALLEL",
    "NOVELOS_WEB_DIST",
    "NOVELOS_WRITER_CONTEXT_MODE",
    "NOVELOS_WRITER_LENGTH_RETRIES",
)

#: 模板必须提供的全部生效键（7 ＋ 6 ＋ 26 ＝ 39）。
REQUIRED_KEYS: tuple[str, ...] = CORE_KEYS + CREDENTIAL_KEYS + OPTIONAL_KEYS

#: 只认**行首**的 ``KEY=值``（注释行一律不算键 ⇒ ``# NOVELOS_X=1`` 不是键）。
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _effective_keys(text: str) -> dict[str, str]:
    """解析模板里的 ``KEY=值`` 行 ⇒ ``{键: 值}``（注释行不计入）。"""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        m = _ASSIGN.match(raw.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _template_text() -> str:
    """读模板；**模板不存在 ⇒ 先断言再读**（守卫式 ⇒ FAILED 而非 ERROR）。"""
    assert TEMPLATE.is_file(), f"缺少 .env.example 模板（BUG-39）：{TEMPLATE}"
    return TEMPLATE.read_text(encoding="utf-8")


def test_env_example_provides_every_required_key():
    """★ BUG-39 判别：模板存在且含**全部 39 个生效键**。"""
    keys = _effective_keys(_template_text())
    missing = [k for k in REQUIRED_KEYS if k not in keys]
    assert not missing, f"模板缺少必含键（{len(missing)} 个）：{missing}"


def test_credential_keys_are_empty_placeholders_only():
    """★ 6 个凭据键**只给空占位** ⇒ 模板里不可能藏真实 token。"""
    keys = _effective_keys(_template_text())
    for k in CREDENTIAL_KEYS:
        assert k in keys, f"模板缺少凭据键：{k}"
        assert keys[k] == "", f"凭据键 {k} 必须只给空占位，实得 {keys[k]!r}"


def test_dangerous_switch_is_comment_only_and_doc_key_absent():
    """★ ``NOVELOS_ALLOW_PUSH`` 只在注释里；``NOVELOS_CLI`` 不入模板。"""
    text = _template_text()
    keys = _effective_keys(text)
    assert "NOVELOS_ALLOW_PUSH" not in keys, (
        "NOVELOS_ALLOW_PUSH 是危险开关（默认不设）⇒ 只能以注释行出现，不得是生效键"
    )
    assert "NOVELOS_CLI" not in text, "NOVELOS_CLI 是纯文档键（只在 SKILL.md）⇒ 不入模板"
