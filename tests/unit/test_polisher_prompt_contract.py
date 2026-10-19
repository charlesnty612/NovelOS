"""polisher 提示词与生产代码的一致性看守（2026-10-19 批次十三）。

事故形状
--------
``docs/agents/prompts/polisher-v1.md`` 三处写「破折号 / 省略号密度 每千字 ≤ 6 处」，
而生产阈值 ``DEFAULT_DASH_THRESHOLD_PER_1K`` 自 2026-09-16 起就是 **1.0**
（阈值 6 → 3.5 → 1.0，**提示词没跟着改**）⇒ **提示词的验收口径与门禁口径互相矛盾**：

* 模型按提示词把密度降到 6 就算「完成」（§9 E-POL-05 就是它的验收标准）；
* 而 ``AI-PUNCT-ABUSE`` 算子按 1.0 判 ⇒ 门禁仍红 ⇒ 润色节点做了一轮「修完还是红」。

同族的「硬编码标签漂移」已在 AGENTS 坑区登记（writer v1→v2 四处口径分叉）。
本批的修法是**升版**（``polisher-v2.md``）而不是原地改 v1——原地改内容不升版会让
两套正文共用一个版本标签。

两条看守
--------
1. **提示词 ↔ 代码 恒等**：ACTIVE 版 polisher 提示词的三处「口径行」里写的阈值数字
   必须**逐字等于** ``DEFAULT_DASH_THRESHOLD_PER_1K``（数字从代码常量读，不手抄）。
   —— 撤任何一处（改回 6 / 改回 3.5 / 改成别的数）都会红。
2. **payload 声明 = DB ACTIVE**：``_polish_node`` 装配的 ``prompt_version`` 必须等于
   prompts 表里该 agent 最高 ACTIVE 行的标签，**不得是硬编码字面量**。
   —— 撤掉 ``active_prompt_label`` 改回硬编码 ``"polisher:v1"`` 就会红。

⚠️ 两条都用**双向**判别（不只查「提示词里有没有某个数」，而是「提示词里写的那个数
必须与代码常量**相等**」）——单向断言（``assert "1.0" in text``）在有人把口径改成
「1.0 / 3.5 并列」时会漏放。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("NOVELOS_CONTEXT_RELEVANCE", "off")

from packages.core.agent_runtime.prompts import PromptRegistry  # noqa: E402
from packages.core.db import apply_migrations  # noqa: E402
from packages.core.quality.ai_patterns_sentence import (  # noqa: E402
    DEFAULT_DASH_THRESHOLD_PER_1K,
)
from packages.workflows.chapter_write.pipeline import _polish_node  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"
PROMPTS_DIR = ROOT / "docs" / "agents" / "prompts"

#: 提示词里**承载口径**的三行（前缀必须逐字命中，否则视为「口径行不见了」——
#: 提示词改版时若顺手删了某一行，本看守要红，而不是「少查一处也算过」）。
_OPERATIVE_LINE_PREFIXES = (
    "  - AI-PUNCT-ABUSE 命中 →",   # §3 Responsibilities
    "3. **破折号 / 省略号密度**",   # §6 行为规则 3
    "5. **E-POL-05 标点密度扫描**",  # §9 验收规则 E-POL-05
)

#: 「≤ 后面的数字」。只认 ≤（不含 ≥），避免把「（≥2 个）」里的 2 读成阈值。
_LE_RE = re.compile(r"≤\s*([0-9]+(?:\.[0-9]+)?)")


def _active_polisher_prompt_path() -> Path:
    """取最高版本的 polisher 提示词文件（与 registry 的取法一致：数字序最大）。"""
    files = list(PROMPTS_DIR.glob("polisher-v*.md"))
    assert files, f"polisher 提示词文件缺失：{PROMPTS_DIR}"
    return max(files, key=lambda p: int(re.search(r"polisher-v(\d+)\.md$", p.name).group(1)))


def _operative_line(text: str, prefix: str) -> str:
    matches = [ln for ln in text.splitlines() if ln.startswith(prefix)]
    assert len(matches) == 1, (
        f"口径行 {prefix!r} 命中 {len(matches)} 次（要求恰好 1）——"
        f"提示词改版若删改了该行，本看守必须红，而不是「少查一处也算过」"
    )
    return matches[0]


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "polisher_prompt.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


# ---------------------------------------------------------------------------
# 1. 提示词 ↔ 代码 恒等
# ---------------------------------------------------------------------------


def test_polisher_prompt_threshold_equals_production_threshold():
    """ACTIVE 版 polisher 提示词三处口径行的阈值 == DEFAULT_DASH_THRESHOLD_PER_1K。"""
    path = _active_polisher_prompt_path()
    text = path.read_text(encoding="utf-8")
    expected = DEFAULT_DASH_THRESHOLD_PER_1K

    for prefix in _OPERATIVE_LINE_PREFIXES:
        line = _operative_line(text, prefix)
        numbers = {float(m.group(1)) for m in _LE_RE.finditer(line)}
        assert numbers == {expected}, (
            f"{path.name} 的口径行阈值 {sorted(numbers)} != 生产阈值 {expected}\n"
            f"    行：{line.strip()}\n"
            f"    ⇒ 提示词与代码分叉（模型会按提示词收手，而门禁按代码判）"
        )


def test_polisher_prompt_declares_same_metric_as_the_operator():
    """口径行必须写明「合计 / 可见字符」——只写「密度」等于没告诉模型在跟什么对齐。"""
    path = _active_polisher_prompt_path()
    text = path.read_text(encoding="utf-8")
    for prefix in _OPERATIVE_LINE_PREFIXES:
        line = _operative_line(text, prefix)
        assert "可见字符" in line, (
            f"{path.name} 口径行未写明分母口径：{line.strip()}\n"
            f"    ⇒ 算子 ``AI-PUNCT-ABUSE`` 的分母是 visible_chars（剔空白、标点计入）"
        )


# ---------------------------------------------------------------------------
# 2. payload 声明 = DB ACTIVE（不得硬编码）
# ---------------------------------------------------------------------------


def test_polish_node_payload_prompt_version_matches_db_active(tmp_path: Path, monkeypatch):
    """_polish_node 装配的 prompt_version == prompts 表该 agent 最高 ACTIVE 标签。"""
    import packages.workflows.chapter_write.pipeline as cw_pipeline

    db_path = _fresh_db(tmp_path)
    PromptRegistry(db_path).sync_from_docs(PROMPTS_DIR)
    _pid, active_label, _content = PromptRegistry(db_path).get_active_prompt("polisher")
    assert active_label.startswith("polisher:v"), active_label

    captured: dict[str, Any] = {}

    def _fake_run_agent(db_path_, agent_name, payload, run_id, **kw):
        captured["agent_name"] = agent_name
        captured["payload"] = payload
        return {
            "schema_version": "polisher-output.v1",
            "prompt_version": active_label,
            "polished_text": "他走了。她没回头。",
            "changes_summary": "降低破折号密度",
        }

    monkeypatch.setattr(cw_pipeline, "run_agent", _fake_run_agent)

    # 预检必须命中（否则 polisher 直接 skipped_clean，压根不装配 payload）。
    # 三个破折号 + 短正文 ⇒ AI-PUNCT-ABUSE 命中率远高于 1.0/千字。
    prose = "他——走了。她——没回头。风——很冷。"
    ctx = {
        "db_path": db_path,
        "run_id": "run_polisher_prompt",
        "chapter_id": "ch_polisher_prompt",
        "writer_output": {"prose": prose},
        "mock_providers": {},  # 非 mock 直通形态（只有 writer 才直通）
    }
    out = _polish_node(ctx)

    assert out.get("polisher_status") == "failed" or captured, (
        f"未捕获到 run_agent 调用（polisher_status={out.get('polisher_status')!r}）"
    )
    assert captured["agent_name"] == "polisher"
    assert captured["payload"]["prompt_version"] == active_label, (
        f"payload 声明 {captured['payload']['prompt_version']!r} != DB ACTIVE "
        f"{active_label!r} ⇒ 硬编码标签漂移（writer v1→v2 同形）"
    )
    assert captured["payload"]["prompt_version"] != "polisher:v1" or active_label == "polisher:v1", (
        "payload 仍硬编码 polisher:v1 —— 升版后模型会照抄回声，而 ai_call_logs 记真值"
    )
