"""签约体检「题材开篇**没检** vs **检过且通过**」必须可区分（D-13 回归）。

背景（D-13 的**原始**形态，已在 2026-10-23 批次修掉）：
``packages/core/signing_check/service.py::_load_project_opening_rules`` 读题材包失败时
``except Exception: return None`` ⇒ 题材开篇整段**静默消失**、无告警、无 errors 字段
⇒ 体检报告外观「全通过」而实际**未检**。同文件 ``word_band_rejected`` 已有显式标记
⇒ 处置不一致。

本文件守的是**核心不变式**（不是逐字实现）：

    「读不到」**绝不**被报成「通过」。

覆盖「读不到」的**两种**形态（都必须是显式不可检）：

1. **读库异常**——``get_project_binding`` 抛错（缺表 / 读库失败）；
2. **绑定在但题材包行缺失**——``bound=true`` 且 ``pack is None``（悬空引用：
   FK-off 旁路删除 / 老库遗留 / 备份恢复残留）。⚠️ 形态 ② 此前落进「未绑定 ⇒
   (None, None)」⇒ 与「压根没绑题材包」**不可区分** ⇒ 报告外观同样是「全通过」。

断言一律打在**真实值**上（顶层键的实际取值 / items 里的实际 level / summary 的实际
计数 / 注入的异常文本本身），不写恒真断言。**不过度触发**由三条反向对照守住
（未绑定 / 绑了且读得到 / 绑了但包本身没声明规则）。

测试**不碰 LLM**、**不碰内容仓**、**不碰活库**；DB 用 pytest 临时目录 + 合成夹具。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.delivery.evidence import collect_project_evidence
from packages.core.delivery.verdict import (
    DELIVERABLE,
    RULE_SIGNING_SEGMENT_UNAVAILABLE,
    derive_chapter_verdict,
)
from packages.core.ids import new_id, now_iso
from packages.core.signing_check.service import format_summary, run_signing_check

#: 「题材开篇不可检」标记的键（与 ``packages.core.signing_check.checks``
#: 的 ``GENRE_OPENING_UNAVAILABLE_KEY`` 同值；此处独立写出，避免测试只跟着常量跑）。
UNAVAILABLE_KEY = "genre_opening_unavailable"

#: 恒在的顶层键（``run_signing_check`` 的返回结构：恒 7 键 + 4 个条件键）。
CORE_KEYS = {
    "project_id",
    "project_name",
    "total_chars",
    "generated_at",
    "items",
    "summary",
    "word_band_source",
}

#: 声明了 ``opening_rules`` 的题材包 payload（题材包读得到 ⇒ 题材开篇段应当出现）。
PACK_WITH_RULES: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "opening_rules": [
        {
            "check_id": "r1",
            "chapter_no": 1,
            "description": "第一章必须出现冲突",
            "requirement": "第一章必须出现打脸冲突",
            "keywords": ["打脸"],
        }
    ],
}

#: 合法但**没声明** ``opening_rules`` 的题材包 payload（正常「没有可查的规则」）。
PACK_WITHOUT_RULES: dict = {"schema_version": "genre-pack.v1.0.0"}


# ---------------------------------------------------------------------------
# 工厂（临时库 + 合成夹具）
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


def _seed_project(db_path: Path, *, name: str = "体检书") -> str:
    """种一个 project + 一章正文（体检要逐章取正文）。"""
    now = now_iso()
    pid = new_id("prj")
    cid = new_id("ch")
    conn = get_connection(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO projects(project_id,name,genre,target_words,status,genre_pack_id,
                created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (pid, name, "科幻", 100_000, "ACTIVE", None, now, now),
        )
        conn.execute(
            """
            INSERT INTO chapters(chapter_id,project_id,number,title,plan_json,status,
                visibility,who_knows,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (cid, pid, 1, "第一章", "{}", "COMMITTED", "VISIBLE", None, now, now),
        )
        conn.execute(
            """
            INSERT INTO drafts(draft_id,chapter_id,version,content,created_by,
                prompt_version,model_id,created_at)
            VALUES (?,?,?,?,?,NULL,NULL,?)
            """,
            (new_id("dr"), cid, 1, "林澈推开门，屋里没有人。", "writer", now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_pack(db_path: Path, payload: dict) -> str:
    """插一个题材包行，返回 pack_id。"""
    now = now_iso()
    pack_id = new_id("gp")
    conn = get_connection(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO genre_packs(pack_id,name,genre_tag,version,payload_json,source_path,
                created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                pack_id,
                "测试题材包",
                "科幻",
                1,
                json.dumps(payload, ensure_ascii=False),
                None,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return pack_id


def _bind(db_path: Path, project_id: str, pack_id: str) -> None:
    """把题材包绑到项目（``projects.genre_pack_id`` 单 slot）。"""
    conn = get_connection(str(db_path))
    try:
        conn.execute(
            "UPDATE projects SET genre_pack_id=? WHERE project_id=?", (pack_id, project_id)
        )
        conn.commit()
    finally:
        conn.close()


def _delete_pack_row_off_fk(db_path: Path, pack_id: str) -> None:
    """旁路删掉题材包行（FK off）⇒ 留下**悬空引用**。

    形态：FK-on 时 ``ON DELETE SET NULL`` 会把绑定列置空（项目自己的写路径不可达此态）；
    FK-off 的旁路删除 / 老库遗留 / 备份恢复残留 ⇒ 绑定列仍指向一个不存在的包。
    """
    conn = get_connection(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM genre_packs WHERE pack_id=?", (pack_id,))
        conn.commit()
    finally:
        conn.close()


def _break_genre_pack_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """让「读项目题材包绑定」抛异常（模拟缺表 / 读库失败）。"""

    def _boom(self, project_id: str):  # noqa: ANN001, ANN202
        raise sqlite3.OperationalError("no such table: genre_packs")

    monkeypatch.setattr(
        "packages.core.signing_check.service.GenrePackService.get_project_binding",
        _boom,
    )


def _unavailable_items(result: dict) -> list[dict]:
    """结果里「题材开篇不可检」条目。"""
    return [it for it in result["items"] if it["key"] == UNAVAILABLE_KEY]


def _genre_items(result: dict) -> list[dict]:
    """结果里所有「题材开篇相关」条目（不可检标记 + 逐条规则判定）。"""
    return [it for it in result["items"] if it["key"].startswith("genre_opening_")]


def _genre_signature(result: dict) -> dict:
    """报告在「题材开篇维度」上的**可判别签名**（用于两两比较）。

    只取与「题材开篇有没有查」有关的字段；``generated_at`` 等易变字段不入签名。
    """
    return {
        "marker": result.get("genre_opening_unavailable", False),
        "error": result.get("genre_opening_error"),
        "section": "genre_opening" in result,
        "genre_items": [it["key"] for it in _genre_items(result)],
    }


# ---------------------------------------------------------------------------
# 1. 读失败 ⇒ 显式不可检标记（字段与值）
# ---------------------------------------------------------------------------


class TestReadFailureMarksUnavailable:
    def test_marker_and_reason_present_with_real_values(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """注入读失败 ⇒ 顶层出现不可检标记 + 真因（不是空串、不是 bool 之外的东西）。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)

        assert result.get("genre_opening_unavailable") is True, (
            "读题材包失败时结果里必须出现显式不可检标记："
            f"{sorted(result)}"
        )
        reason = result.get("genre_opening_error")
        assert reason, "必须带上失败原因（可排查）"
        # 真因来自注入的异常本身（不是硬编码常量）
        assert "OperationalError" in reason, reason
        assert "genre_packs" in reason, reason

    def test_unavailable_item_is_present_and_not_pass(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不可检条目必须在 items 里，且**不得**是 pass（否则外观仍是全通过）。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)

        marked = _unavailable_items(result)
        assert len(marked) == 1, [it["key"] for it in result["items"]]
        assert marked[0]["level"] == "info", marked[0]
        assert "不可检" in marked[0]["detail"], marked[0]
        assert "未执行" in marked[0]["detail"], marked[0]

    def test_bound_pack_row_missing_is_also_marked_unavailable(self, db_path: Path) -> None:
        """⚠️ **读失败的第二种形态**：绑定在、题材包行不在 ⇒ 同样必须显式不可检。

        此前此形态落进「未绑定 ⇒ (None, None)」⇒ 与「没绑题材包」**不可区分**
        ⇒ 报告外观仍是「全通过」（实际未检）。
        """
        pid = _seed_project(db_path)
        pack_id = _insert_pack(db_path, PACK_WITH_RULES)
        _bind(db_path, pid, pack_id)
        _delete_pack_row_off_fk(db_path, pack_id)

        result = run_signing_check(str(db_path), pid)

        assert result.get("genre_opening_unavailable") is True, (
            f"题材包行缺失必须显式标注不可检：{sorted(result)}"
        )
        reason = result.get("genre_opening_error")
        assert reason, "必须带上原因"
        assert pack_id in reason, f"原因里应能看到缺失的 pack_id：{reason}"
        assert "genre_opening" not in result, "没检 ⇒ 不得产出题材开篇判定段"

    def test_bound_pack_row_missing_differs_from_unbound_project(self, db_path: Path) -> None:
        """⚠️ **本次修复的核心不变式**：形态 ② 的报告**不得**与「压根没绑题材包」同形。

        同形 = 读者无法分辨「作者压根没用题材包」与「作者用了个题材包但它没了、
        这次**没检**」⇒ 后者会被读成「没有题材开篇要求，所以全过」。
        ⇒ 两者必须**可区分**：形态 ② 带不可检标记 + 真因，未绑定不带。
        """
        # 对照组：未绑定题材包的项目（同一个库里另起一个项目）
        unbound_pid = _seed_project(db_path, name="未绑定书")
        unbound = run_signing_check(str(db_path), unbound_pid)

        # 实验组：绑定在、题材包行被删
        pid = _seed_project(db_path, name="悬空绑定书")
        pack_id = _insert_pack(db_path, PACK_WITH_RULES)
        _bind(db_path, pid, pack_id)
        _delete_pack_row_off_fk(db_path, pack_id)
        dangling = run_signing_check(str(db_path), pid)

        assert _genre_signature(dangling) != _genre_signature(unbound), (
            "形态 ② 与「未绑定题材包」产出了相同结果 ⇒ "
            f"「没检」与「无需检」不可区分：{_genre_signature(dangling)}"
        )
        # 差异的具体形状（钉在真实值上）
        assert dangling.get("genre_opening_unavailable") is True, dangling
        assert unbound.get("genre_opening_unavailable", False) is False, unbound
        assert "genre_opening_unavailable" not in unbound, unbound
        assert dangling.get("genre_opening_error"), dangling


# ---------------------------------------------------------------------------
# 2. 读失败 ⇒ 不得出现「题材开篇通过」这类结论
# ---------------------------------------------------------------------------


class TestReadFailureNeverClaimsPass:
    def test_no_structured_genre_opening_section(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """读失败 ⇒ **不得**产出题材开篇判定段（段的存在即「查过」的语义）。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)

        assert "genre_opening" not in result, result.get("genre_opening")

    def test_only_the_unavailable_marker_is_present(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """读失败 ⇒ 题材开篇相关的条目**只有**不可检标记那一条。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)

        assert [it["key"] for it in _genre_items(result)] == [UNAVAILABLE_KEY], [
            it["key"] for it in _genre_items(result)
        ]

    def test_no_item_claims_a_genre_rule_was_satisfied(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """读失败 ⇒ 没有任何条目声称「题材开篇规则…满足」（pass 档文案）。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)

        satisfied = [
            it
            for it in result["items"]
            if "题材开篇规则" in it["detail"] and "满足" in it["detail"]
        ]
        assert not satisfied, satisfied
        assert not [it for it in _genre_items(result) if it["level"] == "pass"]


# ---------------------------------------------------------------------------
# 3. 读失败 ⇒ 报告不崩、结构完整、错误可见
# ---------------------------------------------------------------------------


class TestReportStaysIntactOnReadFailure:
    def test_structure_complete_and_error_field_non_empty(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """报告整体仍能产出（恒 7 键齐全）+ summary 计数自洽 + 错误字段非空。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)

        assert CORE_KEYS.issubset(result), sorted(CORE_KEYS - set(result))
        assert set(result["summary"]) == {
            "pass_count",
            "warn_count",
            "fail_count",
            "info_count",
        }, result["summary"]
        assert sum(result["summary"].values()) == len(result["items"]), result["summary"]
        assert result["genre_opening_error"], "错误/告警字段必须非空"

    def test_text_summary_shows_the_marker(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """投稿包内嵌的纯文本摘要里能看到该标记（渲染层不得把它吞掉）。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)
        text = format_summary(result)

        assert UNAVAILABLE_KEY in text, text
        assert "不可检" in text, text


# ---------------------------------------------------------------------------
# 4. 汇总判定：不可检时**不得**声称「全通过」
# ---------------------------------------------------------------------------


class TestSummaryNeverClaimsAllPass:
    def test_pass_count_never_covers_every_item(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """存在不可检项 ⇒ ``pass_count`` 不得等于条目总数（=「全部通过」的口径）。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)
        summary = result["summary"]

        assert summary["pass_count"] < len(result["items"]), summary
        assert summary["info_count"] >= 1, summary
        # 计数与 items 的 level 桶**逐项一致**（不可检条目被算进 info，不算 pass）
        assert summary["pass_count"] == sum(
            1 for it in result["items"] if it["level"] == "pass"
        ), summary
        assert summary["info_count"] == sum(
            1 for it in result["items"] if it["level"] == "info"
        ), summary

    def test_text_summary_count_line_reports_the_info_item(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """摘要首行的计数必须体现那条 info（不可检），不能只显示「通过 N」。"""
        pid = _seed_project(db_path)
        _break_genre_pack_read(monkeypatch)

        result = run_signing_check(str(db_path), pid)
        text = format_summary(result)

        assert f"信息 {result['summary']['info_count']}" in text, text
        assert f"通过 {result['summary']['pass_count']}" in text, text


# ---------------------------------------------------------------------------
# 5. 反向对照：读得到 ⇒ **不得**出现不可检标记（防过度触发 / 防恒真断言）
# ---------------------------------------------------------------------------


class TestReadableGenrePackHasNoMarker:
    def test_bound_and_readable_pack_has_no_marker(
        self, db_path: Path
    ) -> None:
        """题材包**读得到**（且绑定了、声明了规则）⇒ 不得出现不可检标记。

        ⚠️ 判别力核心：这条是「标记出现」的**反向对照**——若标记恒真，此条必红。
        """
        pid = _seed_project(db_path)
        pack_id = _insert_pack(db_path, PACK_WITH_RULES)
        _bind(db_path, pid, pack_id)

        result = run_signing_check(str(db_path), pid)

        assert "genre_opening_unavailable" not in result, result
        assert "genre_opening_error" not in result, result
        assert not _unavailable_items(result)
        # 题材开篇段**确实**在（证明包真被读到了，不是「读失败但没报错」）
        assert "genre_opening" in result, sorted(result)
        assert result["genre_opening"]["rule_count"] == 1, result.get("genre_opening")
        assert [it["key"] for it in _genre_items(result)] == ["genre_opening_r1"], [
            it["key"] for it in _genre_items(result)
        ]

    def test_unbound_project_has_no_marker(self, db_path: Path) -> None:
        """未绑定题材包（本次没有可查的规则）⇒ 不得出现不可检标记。"""
        pid = _seed_project(db_path)

        result = run_signing_check(str(db_path), pid)

        assert "genre_opening_unavailable" not in result, result
        assert not _unavailable_items(result)

    def test_bound_pack_without_opening_rules_has_no_marker(self, db_path: Path) -> None:
        """绑定了题材包但**包本身没声明** opening_rules ⇒ 不得出现不可检标记。

        ⚠️ 防「一律标记」的过度收紧：包读得到、只是没有题材开篇规则 ⇒ 正常缺席，
        不是「读不到」。
        """
        pid = _seed_project(db_path)
        pack_id = _insert_pack(db_path, PACK_WITHOUT_RULES)
        _bind(db_path, pid, pack_id)

        result = run_signing_check(str(db_path), pid)

        assert "genre_opening_unavailable" not in result, result
        assert not _unavailable_items(result)
        assert "genre_opening" not in result, "包没声明规则 ⇒ 判定段缺席（既有口径）"


# ---------------------------------------------------------------------------
# 6. 四种情形两两可区分（本次修复的验收口径）
# ---------------------------------------------------------------------------


class TestFourSituationsArePairwiseDistinguishable:
    """同一套机制下的四种情形，报告必须**两两可区分**。

    | 情形 | 题材包 | 期望「不可检」标记 |
    |---|---|---|
    | ① 读库异常 | 读不到（抛错） | **有** |
    | ② 绑定在、包行不在 | 读不到（悬空引用） | **有** |
    | ③ 未绑定题材包 | 本次无需检 | **无** |
    | ④ 绑定在、包可读 | 检了 | **无** |

    ①②「没检」 vs ③④「无需检 / 检过」必须**分得开**；①②之间也要分得开
    （原因不同：一个是异常，一个是包行缺失）。
    """

    def test_marker_partition_and_pairwise_distinctness(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ③ 未绑定题材包
        p_unbound = _seed_project(db_path, name="情形③未绑定")
        r_unbound = run_signing_check(str(db_path), p_unbound)

        # ④ 绑定在、包可读
        p_readable = _seed_project(db_path, name="情形④可读")
        pack_ok = _insert_pack(db_path, PACK_WITH_RULES)
        _bind(db_path, p_readable, pack_ok)
        r_readable = run_signing_check(str(db_path), p_readable)

        # ② 绑定在、包行不在
        p_dangling = _seed_project(db_path, name="情形②悬空")
        pack_gone = _insert_pack(db_path, PACK_WITH_RULES)
        _bind(db_path, p_dangling, pack_gone)
        _delete_pack_row_off_fk(db_path, pack_gone)
        r_dangling = run_signing_check(str(db_path), p_dangling)

        # ① 读库异常（patch 生效 ⇒ 只影响这一段）
        p_exc = _seed_project(db_path, name="情形①异常")
        _break_genre_pack_read(monkeypatch)
        r_exc = run_signing_check(str(db_path), p_exc)

        sig = {
            "①读库异常": _genre_signature(r_exc),
            "②包行缺失": _genre_signature(r_dangling),
            "③未绑定": _genre_signature(r_unbound),
            "④绑定可读": _genre_signature(r_readable),
        }

        # ① ② 有标记；③ ④ 没有
        assert sig["①读库异常"]["marker"] is True, sig
        assert sig["②包行缺失"]["marker"] is True, sig
        assert sig["③未绑定"]["marker"] is False, sig
        assert sig["④绑定可读"]["marker"] is False, sig

        # ① ② 都**没检** ⇒ 不得产出题材开篇判定段
        assert sig["①读库异常"]["section"] is False, sig
        assert sig["②包行缺失"]["section"] is False, sig
        # ④ 检过 ⇒ 判定段在，且逐条规则条目在
        assert sig["④绑定可读"]["section"] is True, sig
        assert sig["④绑定可读"]["genre_items"] == ["genre_opening_r1"], sig

        # ① 与 ② 的**原因不同**（异常 vs 包行缺失）⇒ 两两可区分
        assert sig["①读库异常"]["error"] != sig["②包行缺失"]["error"], sig
        assert "OperationalError" in sig["①读库异常"]["error"], sig
        assert pack_gone in sig["②包行缺失"]["error"], sig

        # 四种情形的签名**两两不同**
        names = list(sig)
        for i, left in enumerate(names):
            for right in names[i + 1 :]:
                assert sig[left] != sig[right], (
                    f"情形「{left}」与「{right}」产出了相同结果 ⇒ 不可区分：{sig[left]}"
                )


# ---------------------------------------------------------------------------
# 7. 消费方链路的端到端证据（形态 ② 会不会被消费方漏处理？）
# ---------------------------------------------------------------------------

#: 干净章的开篇：冲突词 + 对话句 + 主角名 + 金手指词（黄金三章三项都不落 fail）。
_CLEAN_HEAD = "李晨被人当面羞辱，全场震惊。「你算什么？」他咬牙立誓。系统绑定，面板浮现。"
#: plan_json.expected_word_count 口径下的目标字数（带 ±15% ⇒ 2125~2875）。
_TARGET_VISIBLE = 2500


def _seed_clean_project(db_path: Path) -> str:
    """种一个**干净**项目：1 章 + 正文 + 主角 + 质量报告（无 issue）。

    干净 ⇒ 判定层除「不可检」外不产生任何降级理由 ⇒ 端到端用例可以把
    「唯一的 delta」锁定在不可检上。
    """
    now = now_iso()
    pid = new_id("prj")
    cid = new_id("ch")
    conn = get_connection(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO projects(project_id,name,genre,target_words,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (pid, "悬空绑定交付书", "科幻", 100_000, "ACTIVE", now, now),
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
            (new_id("dr"), cid, 1, _CLEAN_HEAD + "字" * (_TARGET_VISIBLE - len(_CLEAN_HEAD)), "writer", now),
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
    finally:
        conn.close()
    return pid


class TestConsumerPathHandlesFormTwo:
    """形态 ② 的标记**是否真的被消费方读到了**（端到端）。

    ⚠️ 为什么要这条：形态 ② 复用的是形态 ① 的**同一套**标记（顶层
    ``genre_opening_unavailable`` / ``genre_opening_error`` + 同一条 ``info`` 级
    item）⇒ 消费方（``delivery.evidence`` 摊章 → ``delivery.verdict`` 降级）
    **理论上**无需改动。本用例把「理论」变成**实测**：若消费方真的漏了形态 ②，
    这条会红。
    """

    def test_form_two_blocks_deliverable_end_to_end(self, db_path: Path) -> None:
        """形态 ②（绑定在、包行缺失）⇒ 该章**不得** ``deliverable``。"""
        pid = _seed_clean_project(db_path)
        pack_id = _insert_pack(db_path, PACK_WITH_RULES)
        _bind(db_path, pid, pack_id)
        _delete_pack_row_off_fk(db_path, pack_id)

        evidence = collect_project_evidence(str(db_path), pid)
        assert len(evidence.chapters) == 1
        result = derive_chapter_verdict(evidence.chapters[0])

        assert result.verdict != DELIVERABLE, result.verdict
        rule_ids = [r.rule_id for r in result.reasons]
        assert RULE_SIGNING_SEGMENT_UNAVAILABLE in rule_ids, rule_ids
        # 证据层必须**保留**这条项目级 item（不丢弃）
        keys = [i.get("key") for i in evidence.chapters[0].signing.items]
        assert UNAVAILABLE_KEY in keys, keys
