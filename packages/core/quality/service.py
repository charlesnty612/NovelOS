"""QualityService（Sprint 6 下半）—— Quality Engine 评估报告的持久化层与 ctx 组装公共函数。

职责：
- :meth:`QualityService.save_report` —— 把 :class:`QualityReport` 落 ``quality_reports`` 表
  （含 ``scores_json`` = 六子分 + ``_meta``、``issues_json`` = ``Issue[]``）。
- :meth:`QualityService.latest_report` —— 取该 chapter 的最新一份 report。
- :meth:`QualityService.list_reports` —— 按 ``project_id`` 列出全部 report（``created_at``
  降序，``limit`` 默认 50）。
- :func:`compute_char_stats` —— REQ-Q8 字符数统计。
- :func:`build_quality_context` —— 现场组装 :class:`QualityContext`（chapter_commit pipeline
  + API evaluate 端点共用）。
- :func:`load_reference_texts` / :func:`compute_payoff_history` ——
  ``build_quality_context`` 的可重用子函数。

设计要点：
- 与 :mod:`packages.core.quality` 包保持解耦：engine 仍为纯函数；本 service
  负责「报告落地 + 字数 diff 统计 + ctx 组装」，不调 engine —— engine 由
  ``packages/workflows/chapter_commit/pipeline.py`` 与 API 层的
  :mod:`packages.core.api.routers.quality` 现场调 ``engine.evaluate``。
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭（与 S1 服务统一模式）。
- JSON 列读写用 ``json.dumps(ensure_ascii=False)`` / ``json.loads``；
  QualityReport 的 ``_meta`` 在 Python 端属性名是 ``meta``，dump 时通过
  ``model_dump(by_alias=True)`` 输出 ``_meta``。
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.core.quality.models import QualityContext, QualityReport

__all__ = [
    "QualityService",
    "compute_char_stats",
    "build_quality_context",
    "load_reference_texts",
    "compute_payoff_history",
]


class QualityService:
    """Quality 评估报告落库 + 检索。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _dump_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        # scores_json / issues_json 解析
        if "scores_json" in d and isinstance(d["scores_json"], str):
            try:
                d["scores_json"] = json.loads(d["scores_json"])
            except json.JSONDecodeError:
                d["scores_json"] = {}
        if "issues_json" in d and isinstance(d["issues_json"], str):
            try:
                d["issues_json"] = json.loads(d["issues_json"])
            except json.JSONDecodeError:
                d["issues_json"] = []
        return d

    # ---------------------------------------------------------------- save
    def save_report(
        self,
        report: QualityReport,
        *,
        project_id: str,
        chapter_id: str,
        commit_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """把一份 QualityReport 落库。

        - ``project_id`` / ``chapter_id`` 由调用方提供（service 不验证 chapter 归属以
          减少 join；FK 违反由 DB 抛出 ``sqlite3.IntegrityError``）。
        - ``scores_json`` 落六子分 + ``_meta``（``report.model_dump(by_alias=True)``
          输出 ``_meta`` 字段）。
        - ``issues_json`` 落 ``[Issue]``（pydantic dump）。
        - ``report_id`` 不沿用 QualityEngine 已生成的 ID（避免重放覆盖）；用
          ``report.report_id`` 作为业务标识（spec §1.1）。
        """
        dump = report.model_dump(by_alias=True)
        scores = {
            "overall": dump.get("overall"),
            "plot": dump.get("plot"),
            "character": dump.get("character"),
            "continuity": dump.get("continuity"),
            "style": dump.get("style"),
            "pacing": dump.get("pacing"),
            "foreshadowing": dump.get("foreshadowing"),
            "_meta": dump.get("_meta", {}),
        }
        issues = dump.get("issues", []) or []
        # issues 统一 dump 成 JSON 字符串
        scores_json = self._dump_json(scores)
        issues_json = self._dump_json(issues)
        now = now_iso()

        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO quality_reports
                    (report_id, project_id, chapter_id, commit_id, run_id,
                     overall, scores_json, issues_json, created_at)
                VALUES
                    (:report_id, :project_id, :chapter_id, :commit_id, :run_id,
                     :overall, :scores_json, :issues_json, :created_at)
                """,
                {
                    "report_id": report.report_id,
                    "project_id": project_id,
                    "chapter_id": chapter_id,
                    "commit_id": commit_id,
                    "run_id": run_id,
                    "overall": int(report.overall),
                    "scores_json": scores_json,
                    "issues_json": issues_json,
                    "created_at": now,
                },
            )
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- latest
    def latest_report(self, chapter_id: str) -> dict[str, Any] | None:
        """最新一份 report；不存在返回 None。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                """
                SELECT * FROM quality_reports
                WHERE chapter_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (chapter_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row)

    # ----------------------------------------------------------------- list
    def list_reports(
        self,
        project_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出该项目下全部 report，按 ``created_at`` 降序。"""
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                """
                SELECT * FROM quality_reports
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (project_id, int(limit)),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]  # type: ignore[union-attr]


# ============================================================================
# REQ-Q8 字数统计
# ============================================================================


def _diff_added_chars(prev: str, curr: str) -> int:
    """用 :class:`difflib.SequenceMatcher` 计算 ``prev → curr`` 的「新增 + 替换」字符总数。

    与 ``len(curr) - len(prev)`` 不同：本函数能正确处理改写场景——把上一版本中
    被替换的字符长度与新插入字符长度都计入「本版本新增工作量」。这是
    REQ-Q8「单次版本相对上一版本贡献字符数」的语义。

    算法：
    - ``SequenceMatcher.get_opcodes()`` 返回 ``equal`` / ``replace`` / ``delete`` /
      ``insert``；取出 ``replace``（双侧长度取 max，计入工作量）与 ``insert``
      （``curr`` 侧长度 = 0，不计）取 ``len(curr side)``。
    - 同段 ``replace`` 取 ``max(prev_len, curr_len)``：当左侧比右侧长（如删减）
      不计额外工作量；当右侧比左侧长（如扩展）按扩展量计。
    """
    if not prev:
        return len(curr or "")
    if not curr:
        return 0
    sm = difflib.SequenceMatcher(a=prev, b=curr, autojunk=False)
    added = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        curr_len = j2 - j1
        prev_len = i2 - i1
        # 对 replace：取「该版本相较于上一版的扩展量」= max(0, curr_len - prev_len)
        # 对 insert：完整计
        added += max(curr_len, prev_len) if tag == "replace" else curr_len
    return added


def compute_char_stats(db_path: Path | str, chapter_id: str) -> tuple[int, int]:
    """REQ-Q8 字符数统计：返回 ``(ai_chars, human_chars)``。

    读 ``drafts`` 表按 ``version`` 升序，遍历每个版本：

    - ``version == 1``（chapter 的首个版本）：无论 ``created_by`` 是 ``agent:*`` 还是
      ``human``，都按 ``len(content)`` 计到对应桶里——首个版本没有「上一版本」可 diff，
      全量计入。
    - 后续版本（``version >= 2``）：按 ``created_by`` 派发到 ``ai_chars`` 或
      ``human_chars`` 桶；用 :func:`_diff_added_chars` 计算该版本相对于「版本序上一份
      draft（含任何 ``created_by``）」的新增工作量。

    这样语义与任务书给定的「首个版本全量计 / 后续版本 diff 增量计」一致；diff 即便
    跨桶（AI→人工 或 人工→AI）也能正确归到本版本对应桶。
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT version, content, created_by
            FROM drafts
            WHERE chapter_id = ?
            ORDER BY version ASC
            """,
            (chapter_id,),
        ).fetchall()
    finally:
        conn.close()

    ai_chars = 0
    human_chars = 0
    prev_content = ""
    for idx, r in enumerate(rows):
        content = r["content"] or ""
        created_by = r["created_by"] or ""
        is_first = idx == 0
        if is_first:
            delta = len(content)
        else:
            delta = _diff_added_chars(prev_content, content)
        if created_by.startswith("agent:"):
            ai_chars += delta
        elif created_by == "human":
            human_chars += delta
        # 其它 created_by 忽略
        prev_content = content
    return ai_chars, human_chars


# ============================================================================
# ctx 组装公共函数（pipeline + API evaluate 复用）
# ============================================================================


def load_reference_texts(db_path: str | Path, project_id: str) -> list[str]:
    """从项目目录 ``<db 父目录>/references/<project_id>/*.txt`` 读参照书。

    - ``project_id`` 不在 ``[A-Za-z0-9_-]`` 白名单内 → 视为「无参照目录」返回空列表
      （安全审计 P2-2：避免任意路径穿越如 ``../evil``）。
    - 目录不存在 → 空列表（info）。
    - 文件以 UTF-8 文本逐行累加到返回 list（每文件一整个 str 条目）。
    - 文件读取异常 → 跳过该文件（log warning 但不阻断）。
    """
    if re.fullmatch(r"[A-Za-z0-9_\-]+", project_id) is None:
        return []
    db_p = Path(db_path)
    refs_dir = db_p.parent / "references" / project_id
    if not refs_dir.is_dir():
        return []
    out: list[str] = []
    for txt in sorted(refs_dir.glob("*.txt")):
        try:
            out.append(txt.read_text(encoding="utf-8"))
        except OSError:
            continue
    return out


def compute_payoff_history(db_path: str | Path, project_id: str, limit: int = 5) -> list[int]:
    """近 ``limit`` 章每章 payoff 计数（resolved_hooks + paid debts 数）。

    算法：
    - 取该项目下 chapters 按 ``number`` 降序、过滤 ``status='COMMITTED'``（与任务书
      「最近 5 章每章 commits 里 delta_json 的 ...」对齐——已 COMMITTED 才有 commits）。
    - 对每章：取该 chapter 在 commits 表的最新一行（按 ``resulting_state_version DESC``）；
      读 ``delta_id`` → ``state_deltas.payload_json``；
      计算 ``len(resolved_hooks) + len([d for d in debt_changes if d.get('status_after')=='paid'])``。
    - 章节按 number 升序返回（任务书「按 number 排序取最近 5 个已 COMMITTED 章」）。
    """
    conn = get_connection(db_path)
    try:
        chap_rows = conn.execute(
            """
            SELECT chapter_id, number FROM chapters
            WHERE project_id = ? AND status = 'COMMITTED'
            ORDER BY number DESC LIMIT ?
            """,
            (project_id, int(limit)),
        ).fetchall()
        chap_rows = list(reversed(chap_rows))
        history: list[int] = []
        for ch in chap_rows:
            row = conn.execute(
                """
                SELECT delta_id FROM commits
                WHERE chapter_id = ?
                ORDER BY resulting_state_version DESC LIMIT 1
                """,
                (ch["chapter_id"],),
            ).fetchone()
            if row is None:
                history.append(0)
                continue
            payload_row = conn.execute(
                "SELECT payload_json FROM state_deltas WHERE delta_id = ?",
                (row["delta_id"],),
            ).fetchone()
            if payload_row is None:
                history.append(0)
                continue
            try:
                payload = json.loads(payload_row["payload_json"] or "{}")
            except json.JSONDecodeError:
                history.append(0)
                continue
            if not isinstance(payload, dict):
                history.append(0)
                continue
            resolved = len(payload.get("resolved_hooks") or [])
            debts = payload.get("debt_changes") or []
            paid = sum(
                1 for d in debts
                if isinstance(d, dict) and d.get("status_after") == "paid"
            )
            history.append(int(resolved + paid))
    finally:
        conn.close()
    return history


def _read_latest_draft(db_path: str | Path, chapter_id: str) -> str:
    """读该 chapter 最新一份 draft 的 content（按 version DESC）。无 draft → 空串。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT content FROM drafts
            WHERE chapter_id = ?
            ORDER BY version DESC LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    return (row["content"] if row else "") or ""


def _read_chapter_plan(db_path: str | Path, chapter_id: str) -> dict[str, Any]:
    """读 chapters.plan_json 字典；schema 允许空 dict。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["plan_json"]:
        return {}
    try:
        v = json.loads(row["plan_json"])
    except json.JSONDecodeError:
        return {}
    return v if isinstance(v, dict) else {}


def _read_chapter_number(db_path: str | Path, chapter_id: str) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT number FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    return int(row["number"]) if row else 0


def build_quality_context(
    db_path: str | Path,
    *,
    project_id: str,
    chapter_id: str,
    delta: dict[str, Any] | None,
    snapshot_pre: dict[str, Any] | None,
    run_id: str | None = None,
) -> QualityContext:
    """为 chapter + project 现场组装 :class:`QualityContext`。

    参数：
    - ``delta`` —— Observer 业务 delta dict（可能为 None，此时默认为空 dict）。
    - ``snapshot_pre`` —— :class:`StoryStateService.get_current_state` 输出；
      缺省由调用方提供（pipeline 内已有；evaluate 端点需当场拉）。
    - ``run_id`` —— workflow_run_id（仅回显）。

    该函数为 chapter_commit pipeline 与 API evaluate 端点共用，避免重复实现。
    """
    ai_chars, human_chars = compute_char_stats(db_path, chapter_id)
    plan = _read_chapter_plan(db_path, chapter_id)
    draft = _read_latest_draft(db_path, chapter_id)
    reference_texts = load_reference_texts(db_path, project_id)
    payoff_history = compute_payoff_history(db_path, project_id, limit=5)
    chapter_number = _read_chapter_number(db_path, chapter_id)

    return QualityContext(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        draft=draft,
        plan=plan,
        snapshot_pre=snapshot_pre or {},
        delta=delta or {},
        payoff_history=payoff_history,
        reference_texts=reference_texts,
        whitelist=[],
        ai_chars=ai_chars,
        human_chars=human_chars,
        run_id=run_id,
    )
