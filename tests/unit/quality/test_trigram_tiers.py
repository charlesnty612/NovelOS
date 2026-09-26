"""trigram 重复率**两档**阈值（2026-09-18 重定；本文件是阈值口径的判别测试）。

背景（先验判断被推翻，AGENTS.md 留痕规矩）：``RULE_STYLE_REPETITION_TRIGRAM`` 曾因
**一个**观测样本（事故章 30.37%）被放进 ``CONFIRM_RULES``，阈值 0.08。次日实测证明
0.08 落在**正常分布内部**：

- 人类锚点书（内容仓榜一《快穿之人渣洗白手册》侯府弧 19 章）：mean 0.0754 /
  median 0.0787 / min 0.0535 / **max 0.0949**，9/19 章超 0.08；
- 生成侧（``data/novelos.db`` 92 章最新草稿）：min 0.0619 / p50 0.1333 / p90 0.1749 /
  **max 0.2097**，仅 2/92 章低于 0.08。

按 0.08 要求签字 ⇒ 人写的稿子也要签字（橡皮图章）。现分两档，均取自实测：

| 档 | 阈值 | 取法 | 实测后果 |
|---|---|---|---|
| warn | ``STYLE_TRIGRAM_WARN_THRESHOLD`` = 0.16 | 生成侧 p85 = 0.1621 | 人类锚点书 0 条命中 |
| confirm | ``STYLE_TRIGRAM_CONFIRM_THRESHOLD`` = 0.25 | 生成侧 max 0.2097 与事故 0.3037 的几何中点 | 92 章生成侧 0 条命中 |

本文件的四个 fixture 各代表一个实测带（数值为**实测**，写在用例名与注释里）：
0.094（人类锚点书上沿）→ 0.138（生成侧中位）→ 0.163 / 0.200（生成侧偏上~上沿）
→ 0.335（事故量级）。复算口径：对每章最新草稿调 ``scoring._trigram_repetition_rate``
（生成侧 = ``data/novelos.db`` 全项目，人类侧 = 内容仓榜一 txt），阈值注释见
``scoring.STYLE_TRIGRAM_WARN_THRESHOLD`` / ``STYLE_TRIGRAM_CONFIRM_THRESHOLD``。
"""

from __future__ import annotations

import pytest

from packages.core.quality.scoring import (
    STYLE_TRIGRAM_CONFIRM_THRESHOLD,
    STYLE_TRIGRAM_WARN_THRESHOLD,
    _trigram_repetition_rate,
    score_style,
)
from tests.unit.neutral_prose import neutral_prose

# ---------------------------------------------------------------------------
# 实测基准（2026-09-18 复算；改阈值前先复核这几个数字）
# ---------------------------------------------------------------------------

HUMAN_MAX_MEASURED = 0.0949  # 人类锚点书 19 章最大（第 7 章）
HUMAN_MEAN_MEASURED = 0.0754  # 人类锚点书均值（7.4% 均带）
MACHINE_MEDIAN_MEASURED = 0.1333  # 生成侧 92 章 p50
MACHINE_MAX_MEASURED = 0.2097  # 生成侧 92 章最大（prj_5be9256febad ch17）
# P0-1 事故**那一版**（ch_92bac068ff0d v6 / 2374 字，仍在库里，可只读复算）。
# 同章其余 12 版是 0.085~0.130 —— 事故是单版异常，不是整章问题。
INCIDENT_MEASURED = 0.3037

# 事故**量级**正文：同一段 3 句素材在章内逐字复述 9 遍（事故形状 = 9 段逐字重复）。
# 实测：trigram 0.3348 / 13 字 shingle 0.2057（对照事故章 0.3037 / 0.203）。
INCIDENT_DUPLICATED_PARAGRAPH = (
    "破屋的灯还亮着，他把窗纸又糊了一遍，指节在窗棂上停了停。"
    "屋里没有炭火，铜壶里的水早凉了，他仍旧坐着没动。"
    "窗外的风把纸缝吹得响，他把袖口拢紧，又低头看那半页旧账。"
)


def incident_prose() -> str:
    """事故量级正文（确定性）：``neutral_prose(2000)`` 里均布插入 9 遍同一段素材。"""
    parts = neutral_prose(2000).split("\n\n")
    step = max(1, len(parts) // 10)
    for i in range(9):
        parts.insert(min(len(parts), (i + 1) * step + i), INCIDENT_DUPLICATED_PARAGRAPH)
    return "\n\n".join(parts)


def _trigram_issues(draft: str):
    _score, issues = score_style(draft)
    return [i for i in issues if i.rule_id == "RULE_STYLE_REPETITION_TRIGRAM"]


def _rate(draft: str) -> float:
    return _trigram_repetition_rate(draft)


# ---------------------------------------------------------------------------
# 阈值本身：必须把实测分布分开（数字与数据自洽）
# ---------------------------------------------------------------------------


def test_thresholds_separate_the_measured_distributions():
    """不变式：人类上沿 < warn < 生成上沿 < confirm < 事故值。

    这条断言就是「不许拿出与数据矛盾的数字」的机器版本：warn 若落回人类带内
    （旧值 0.08）⇒ 人稿也要签字；confirm 若低于生成上沿 ⇒ 正常生成章也要签字。
    """
    assert STYLE_TRIGRAM_WARN_THRESHOLD == 0.16
    assert STYLE_TRIGRAM_CONFIRM_THRESHOLD == 0.25
    assert (
        HUMAN_MEAN_MEASURED
        < HUMAN_MAX_MEASURED
        < STYLE_TRIGRAM_WARN_THRESHOLD
        < MACHINE_MAX_MEASURED
        < STYLE_TRIGRAM_CONFIRM_THRESHOLD
        < INCIDENT_MEASURED
    )


# ---------------------------------------------------------------------------
# 四个带：无 issue / 无 issue / warn / warn / confirm
# ---------------------------------------------------------------------------


def test_human_band_0094_gets_no_trigram_issue_at_all():
    """人类带（实测 0.0940 ≈ 锚点书 max 0.0949）⇒ 连 warn 都不该出。

    人写的稿子被要求「接受重复」是本缺陷的原症状。
    """
    draft = neutral_prose(1300)
    rate = _rate(draft)
    assert 0.085 <= rate <= HUMAN_MAX_MEASURED, f"fixture 不在人类带内：{rate:.4f}"
    assert _trigram_issues(draft) == []


def test_machine_median_band_0138_gets_no_trigram_issue():
    """生成侧中位（实测 0.1376 ≈ p50 0.1333）⇒ 无 issue（正常生成章不该亮灯）。"""
    draft = neutral_prose(2000)
    rate = _rate(draft)
    assert MACHINE_MEDIAN_MEASURED - 0.02 <= rate <= MACHINE_MEDIAN_MEASURED + 0.02
    assert _trigram_issues(draft) == []


@pytest.mark.parametrize(
    ("chars", "expected_rate"),
    [(2400, 0.1631), (3000, 0.2001)],
)
def test_machine_upper_band_gets_warn_only(chars: int, expected_rate: float):
    """生成侧偏上~上沿（实测 0.1631 / 0.2001；后者≈ 92 章 max 0.2097）⇒ 至多 warn。

    warn 档 = 出 warning issue、``gate=auto``：看得见、但**没有任何后果**
    （不需要签字、不阻断提交）。这是「点名但不设闸」的分层。
    """
    draft = neutral_prose(chars)
    rate = _rate(draft)
    assert abs(rate - expected_rate) < 0.005, f"fixture 漂了：{rate:.4f}"
    assert STYLE_TRIGRAM_WARN_THRESHOLD < rate <= STYLE_TRIGRAM_CONFIRM_THRESHOLD

    issues = _trigram_issues(draft)
    assert len(issues) == 1, issues
    assert issues[0].severity == "warning"
    assert issues[0].gate != "confirm"
    assert issues[0].gate == "auto"
    assert issues[0].evidence_refs, "warn 档也要能看见重复了什么"


def test_incident_band_0335_is_confirm_tier():
    """事故量级（实测 0.3348，对照事故章 0.3037）⇒ ``gate="confirm"``。

    severity 仍是 warning（不引入 error 档）：后果差异只体现在 gate 轴。
    """
    draft = incident_prose()
    rate = _rate(draft)
    assert rate > STYLE_TRIGRAM_CONFIRM_THRESHOLD, f"fixture 不在事故带：{rate:.4f}"
    assert rate > INCIDENT_MEASURED - 0.05, f"应与事故同量级，实际 {rate:.4f}"

    issues = _trigram_issues(draft)
    assert len(issues) == 1, issues
    assert issues[0].severity == "warning", "confirm 档不靠 severity 表达"
    assert issues[0].gate == "confirm"
    assert issues[0].evidence_refs, "要签字就必须给出「在签什么」"
    assert all("×" in ref for ref in issues[0].evidence_refs or [])
