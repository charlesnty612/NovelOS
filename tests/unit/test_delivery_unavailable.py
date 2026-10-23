"""交付判定「不可检」口径回归测试（Task 6 返工 · concern 2）。

背景（两条**已落地**的机制在本层被折叠掉）：

1. **D-13（题材开篇不可检）**：`packages.core.signing_check.checks` 产出
   ``genre_opening_unavailable`` 条目（``info`` 级）⇒ 读题材包失败时「题材开篇
   整段没跑」这件事**有**标记。但交付层 :func:`signing_items_by_chapter` 只把
   **能映射章号**的 item 摊到章 ⇒ 这条**项目级** item 被**静默丢弃** ⇒
   判定层看到的是「体检跑了、这一章有 pass 项」⇒ **「没检」与「检过且通过」
   不可区分** ⇒ 该章可能拿到 ``deliverable``（把「没检」当成「没问题」）。

2. **Task 1（C-1 / D-6，正文读不到）**：``resolve_draft`` 读失败时抛
   :class:`packages.domain.chapter.draft_resolver.DraftResolutionError`（不再是
   ``None``）。但交付层 :func:`_length_evidence_map` 用**同一个 blanket
   ``except Exception``** 把「读不到」与「该章没有草稿」折叠 ⇒ 故障原因只剩
   一句挂在**证据源**上的文案，章级理由的 ``evidence`` 是 ``None``
   ⇒ 作者在章级看不到「草稿表不可用」这个真因（Task 1 需求 3(b) 点名交给本任务）。

覆盖：

- 证据层：项目级「不可检」item **不再被丢弃**（摊到在范围章 1~3）；
- 判定层：「不可检」**必须**产生 ``not_evaluated`` 理由 ⇒ **阻止 ``deliverable``**；
- 反向不变量：可检（无不可检项）⇒ 仍 ``deliverable``（不得过度收紧）；
- 长度侧：「读不到」与「没有草稿」**严格区分**（两条不同的 rule_id）。

测试**不碰 LLM**、**不碰内容仓**；DB 用 pytest 临时目录。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.delivery.evidence import (
    collect_project_evidence,
    signing_items_by_chapter,
)
from packages.core.delivery.verdict import (
    DELIVERABLE,
    NEEDS_WORK,
    RULE_LENGTH_UNAVAILABLE,
    RULE_LENGTH_UNMEASURED,
    RULE_SIGNING_SEGMENT_UNAVAILABLE,
    ChapterEvidence,
    LengthEvidence,
    QualityEvidence,
    SigningEvidence,
    derive_chapter_verdict,
)
from packages.core.ids import new_id, now_iso
from packages.core.quality.wordcount import classify_prose_length
from packages.core.signing_check.checks import GENRE_OPENING_UNAVAILABLE_KEY

#: 干净章的开篇：冲突词 + 对话句 + 主角名 + 金手指词（黄金三章三项都不落 fail）。
_CLEAN_HEAD = "李晨被人当面羞辱，全场震惊。「你算什么？」他咬牙立誓。系统绑定，面板浮现。"
#: plan_json.expected_word_count 口径下的目标字数（带 ±15% ⇒ 2125~2875）。
_TARGET_VISIBLE = 2500


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def _clean_prose(visible: int = _TARGET_VISIBLE) -> str:
    pad = max(0, visible - len(_CLEAN_HEAD))
    return _CLEAN_HEAD + "字" * pad


def _length_evidence(visible: int = _TARGET_VISIBLE, target: int = _TARGET_VISIBLE) -> LengthEvidence:
    """按真实口径造字数证据（tier 由 ``classify_length_tier`` 推导，不手写）。"""
    from packages.core.delivery.verdict import classify_length_tier

    classified = classify_prose_length("字" * visible, target)
    return LengthEvidence(
        visible_chars=int(classified["visible_chars"]),
        target_word_count=int(classified["target"]),
        band_low=int(classified["band_low"]),
        band_high=int(classified["band_high"]),
        deviation_pct=float(classified["deviation_pct"]),
        status=str(classified["status"]),
        tier=classify_length_tier(classified, target),
    )


def _clean_quality() -> QualityEvidence:
    """无 issue 的质量报告（干净章 ⇒ 不产生任何质量理由）。"""
    return QualityEvidence(
        report_id="qr_clean",
        overall=90,
        evaluated_at="2026-10-23T00:00:00+00:00",
        draft_version=1,
        gate_summary=None,
        accepted_override=None,
        issues=(),
    )


def _unavailable_item() -> dict:
    """D-13 的「题材开篇检查不可检」条目（``info`` 级）。"""
    return {
        "key": GENRE_OPENING_UNAVAILABLE_KEY,
        "level": "info",
        "detail": "题材开篇规则**不可检**：题材包读取失败（OperationalError: no such table）",
        "advice": "检查题材包绑定与运行库 genre_packs 表是否可用，再重新体检",
    }


def _chapter_evidence(signing: SigningEvidence, **kwargs) -> ChapterEvidence:
    """一章的证据（默认：干净质量 + 带内字数 + COMMITTED）。"""
    payload: dict = {
        "chapter_id": "ch_1",
        "number": 1,
        "title": "第一章",
        "status": "COMMITTED",
        "draft_version": 1,
        "quality": _clean_quality(),
        "length": _length_evidence(),
        "signing": signing,
    }
    payload.update(kwargs)
    return ChapterEvidence(**payload)


# ---------------------------------------------------------------------------
# 1. 证据层：项目级「不可检」item 不再被丢弃
# ---------------------------------------------------------------------------


class TestUnavailableItemIsNotDropped:
    def test_unavailable_item_is_mapped_to_in_scope_chapters(self) -> None:
        """项目级「不可检」item ⇒ 摊到在范围章（1~3），不是被丢掉。"""
        result = {"items": [_unavailable_item()]}

        by_chapter = signing_items_by_chapter(result)

        assert sorted(by_chapter) == [1, 2, 3], by_chapter
        for number in (1, 2, 3):
            keys = [i["key"] for i in by_chapter[number]]
            assert GENRE_OPENING_UNAVAILABLE_KEY in keys, (number, keys)

    def test_unavailable_item_keeps_its_detail(self) -> None:
        """摊过去的条目**保留 detail**（作者要看到「为什么不可检」）。"""
        result = {"items": [_unavailable_item()]}

        by_chapter = signing_items_by_chapter(result)

        detail = by_chapter[1][0]["detail"]
        assert "不可检" in detail, detail

    def test_other_project_level_items_are_still_not_mapped(self) -> None:
        """反向不变量：其余项目级项（全文统计口径）**仍不摊到单章**。

        ⚠️ 防过度收紧：不可检项是**项目级事实**，但 ``echo_words`` /
        ``signing_window`` 是全文统计、不是某章的门槛 ⇒ 不该摊。
        """
        result = {
            "items": [
                {"key": "echo_words", "level": "warn", "detail": "d", "advice": "a"},
                {"key": "signing_window", "level": "info", "detail": "d", "advice": "a"},
                {"key": "empty", "level": "info", "detail": "d", "advice": "a"},
            ]
        }

        by_chapter = signing_items_by_chapter(result)

        assert by_chapter == {}, by_chapter


# ---------------------------------------------------------------------------
# 2. 判定层：「不可检」产生理由并阻止 deliverable
# ---------------------------------------------------------------------------


class TestUnavailableBlocksDeliverable:
    def test_unavailable_item_produces_not_evaluated_reason(self) -> None:
        """「不可检」⇒ 落一条 ``not_evaluated`` 理由（不是静默通过）。"""
        signing = SigningEvidence(
            scope="in_scope", items=(_unavailable_item(),), note="n"
        )

        result = derive_chapter_verdict(_chapter_evidence(signing))

        reasons = {r.rule_id: r for r in result.reasons}
        assert RULE_SIGNING_SEGMENT_UNAVAILABLE in reasons, [r.rule_id for r in result.reasons]
        assert reasons[RULE_SIGNING_SEGMENT_UNAVAILABLE].status == "not_evaluated"
        assert "不可检" in reasons[RULE_SIGNING_SEGMENT_UNAVAILABLE].message

    def test_unavailable_item_blocks_deliverable(self) -> None:
        """「不可检」⇒ 章**不得**是 ``deliverable``（⇒ needs_work）。"""
        signing = SigningEvidence(
            scope="in_scope", items=(_unavailable_item(),), note="n"
        )

        result = derive_chapter_verdict(_chapter_evidence(signing))

        assert result.verdict != DELIVERABLE, result.verdict
        assert result.verdict == NEEDS_WORK, result.verdict
        assert result.blocking_reason is not None
        assert result.blocking_reason.rule_id == RULE_SIGNING_SEGMENT_UNAVAILABLE

    def test_reverse_control_without_unavailable_item_stays_deliverable(
        self,
    ) -> None:
        """反向不变量：体检可检（只有 pass 项）⇒ 仍 ``deliverable``。

        ⚠️ 防「一律降级」的过度收紧：不可检**才**降级；可检时判定不得被拖累。
        """
        signing = SigningEvidence(
            scope="in_scope",
            items=({"key": "ch1_conflict_300", "level": "pass", "detail": "d", "advice": "a"},),
            note="n",
        )

        result = derive_chapter_verdict(_chapter_evidence(signing))

        assert result.verdict == DELIVERABLE, [r.as_dict() for r in result.reasons]
        assert result.reasons == ()


# ---------------------------------------------------------------------------
# 3. 长度侧：「读不到」与「没有草稿」严格区分
# ---------------------------------------------------------------------------


class TestLengthUnavailableIsDistinctFromNoDraft:
    def test_unavailable_reason_yields_its_own_rule_id(self) -> None:
        """「读不到」⇒ ``DELIVERY_LENGTH_UNAVAILABLE`` + 带上真因。"""
        reason = "正文读不到（草稿表不可用）：no such table: drafts"

        result = derive_chapter_verdict(
            _chapter_evidence(
                SigningEvidence(scope="in_scope", items=(), note="n"),
                length=None,
                length_unavailable_reason=reason,
            )
        )

        rules = {r.rule_id: r for r in result.reasons}
        assert RULE_LENGTH_UNAVAILABLE in rules, [r.rule_id for r in result.reasons]
        assert rules[RULE_LENGTH_UNAVAILABLE].status == "not_evaluated"
        assert rules[RULE_LENGTH_UNAVAILABLE].evidence == reason
        assert result.verdict == NEEDS_WORK, result.verdict

    def test_no_draft_keeps_the_existing_rule_id(self) -> None:
        """反向不变量：「该章确实没有草稿」⇒ 仍是 ``DELIVERY_LENGTH_UNMEASURED``。

        ⚠️ Task 1 的硬要求：别把「没有草稿」也变成故障口径。
        """
        result = derive_chapter_verdict(
            _chapter_evidence(
                SigningEvidence(scope="in_scope", items=(), note="n"),
                length=None,
                length_unavailable_reason=None,
            )
        )

        rules = {r.rule_id: r for r in result.reasons}
        assert RULE_LENGTH_UNMEASURED in rules, [r.rule_id for r in result.reasons]
        assert RULE_LENGTH_UNAVAILABLE not in rules, [r.rule_id for r in result.reasons]


# ---------------------------------------------------------------------------
# 4. 端到端：证据层 + 判定层（临时库，真实取数）
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


def _seed_clean_project(db_path: Path) -> str:
    """种一个**干净**项目：1 章 + 正文 + 主角 + 质量报告（无 issue）。

    干净 ⇒ 判定层除「不可检」外不产生任何降级理由 ⇒ 端到端用例可以把
    「唯一的 delta」锁定在不可检上。
    """
    now = now_iso()
    pid = new_id("prj")
    cid = new_id("ch")
    conn = get_connection(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT INTO projects(project_id,name,genre,target_words,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (pid, "不可检交付书", "科幻", 100_000, "ACTIVE", now, now),
    )
    conn.execute(
        """
        INSERT INTO characters(character_id,project_id,name,role,core_json,visibility,
            who_knows,created_at,updated_at)
        VALUES (?,?,?,'protagonist','{}','VISIBLE',NULL,?,?)
        """,
        (new_id("char"), pid, "李晨", now, now),
    )
    conn.execute(
        """
        INSERT INTO chapters(chapter_id,project_id,number,title,plan_json,status,
            visibility,who_knows,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            cid,
            pid,
            1,
            "第一章",
            json.dumps({"expected_word_count": _TARGET_VISIBLE}, ensure_ascii=False),
            "COMMITTED",
            "VISIBLE",
            None,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO drafts(draft_id,chapter_id,version,content,created_by,
            prompt_version,model_id,created_at)
        VALUES (?,?,?,?,?,NULL,NULL,?)
        """,
        (new_id("dr"), cid, 1, _clean_prose(), "writer", now),
    )
    conn.execute(
        """
        INSERT INTO quality_reports(report_id,project_id,chapter_id,commit_id,run_id,
            overall,scores_json,issues_json,created_at,draft_version)
        VALUES (?,?,?,NULL,NULL,?,?,?,?,?)
        """,
        (
            new_id("qr"),
            pid,
            cid,
            90,
            json.dumps({"overall": 90, "_meta": {"evaluated_at": now}}, ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            now,
            1,
        ),
    )
    conn.commit()
    conn.close()
    return pid


def _break_genre_pack_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """让「读项目题材包绑定」抛异常（模拟缺表 / 读库失败）。"""

    def _boom(self, project_id: str):  # noqa: ANN001, ANN202
        raise sqlite3.OperationalError("no such table: genre_packs")

    monkeypatch.setattr(
        "packages.core.signing_check.service.GenrePackService.get_project_binding",
        _boom,
    )


class TestEndToEndUnavailable:
    def test_clean_chapter_is_deliverable_when_genre_read_works(
        self, db_path: Path
    ) -> None:
        """对照组：读题材包正常 ⇒ 该章 ``deliverable``（不可检**没**发生时）。"""
        pid = _seed_clean_project(db_path)

        evidence = collect_project_evidence(str(db_path), pid)
        assert len(evidence.chapters) == 1
        result = derive_chapter_verdict(evidence.chapters[0])

        assert result.verdict == DELIVERABLE, [r.as_dict() for r in result.reasons]

    def test_genre_read_failure_blocks_deliverable(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """故障注入：读题材包失败 ⇒ 该章**不得** ``deliverable``。

        ⚠️ 这是 concern 2 的核心判别：同一个 fixture，唯一变量是「题材包读失败」
        ⇒ 判定必须从 ``deliverable`` 变成 ``needs_work``。
        """
        pid = _seed_clean_project(db_path)
        _break_genre_pack_read(monkeypatch)

        evidence = collect_project_evidence(str(db_path), pid)
        result = derive_chapter_verdict(evidence.chapters[0])

        assert result.verdict != DELIVERABLE, result.verdict
        assert result.verdict == NEEDS_WORK, result.verdict
        rule_ids = [r.rule_id for r in result.reasons]
        assert RULE_SIGNING_SEGMENT_UNAVAILABLE in rule_ids, rule_ids
        # 证据层必须**保留**这条项目级 item（不丢弃）
        keys = [i.get("key") for i in evidence.chapters[0].signing.items]
        assert GENRE_OPENING_UNAVAILABLE_KEY in keys, keys

    def test_genre_read_failure_marks_the_source_unreachable(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """证据源「诚实面」：题材开篇不可检 ⇒ ``signing_check`` 源标不可达 + 说清原因。"""
        pid = _seed_clean_project(db_path)
        _break_genre_pack_read(monkeypatch)

        evidence = collect_project_evidence(str(db_path), pid)

        source = {s["key"]: s for s in evidence.sources}["signing_check"]
        assert source["reachable"] is False, source
        assert "不可检" in source["detail"], source

    def test_drafts_table_unavailable_is_reported_per_chapter(
        self, db_path: Path
    ) -> None:
        """故障注入：``drafts`` 表不可用 ⇒ 章级带**真因**的不可检理由。

        ⚠️ 改造前：blanket ``except Exception`` 把整源折成一句「字数测量失败：…」
        ⇒ 章级理由是 ``DELIVERY_LENGTH_UNMEASURED`` 且 ``evidence=None``
        ⇒ 作者看不到「草稿表不可用」。
        """
        pid = _seed_clean_project(db_path)
        conn = get_connection(str(db_path))
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE drafts")
            conn.commit()
        finally:
            conn.close()

        evidence = collect_project_evidence(str(db_path), pid)
        chapter = evidence.chapters[0]

        assert chapter.length_unavailable_reason, "章级必须带「读不到」的真因"
        assert "草稿表不可用" in chapter.length_unavailable_reason
        result = derive_chapter_verdict(chapter)
        rules = {r.rule_id: r for r in result.reasons}
        assert RULE_LENGTH_UNAVAILABLE in rules, [r.rule_id for r in result.reasons]
        assert rules[RULE_LENGTH_UNAVAILABLE].evidence
        assert result.verdict != DELIVERABLE, result.verdict
