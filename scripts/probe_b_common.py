"""B 档实证探针 · 共用工具（2026-11-04 B 档逐条实证批）。

只读仓内源码 + 只写**仓外临时目录**（tempfile.mkdtemp ⇒ 系统 Temp）。
不得写仓内任何文件；不发起任何真实 LLM 调用。

运行方式（必须用项目 .venv 解释器、从仓根跑）::

    .venv/Scripts/python.exe scripts/probe_b1_review_commit.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

#: 探针脚本位于 <repo>/scripts/ ⇒ 仓根 = 上一级。
REPO_ROOT = Path(__file__).resolve().parents[1]

# 关键：可编辑安装指向主仓；必须把**本工作树**的仓根插到 sys.path 最前，
# 否则 `import packages` 会解析到主仓而非本树。
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.core.config import Settings  # noqa: E402
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402


def make_temp_db(tag: str) -> tuple[str, Path]:
    """在系统 Temp 下建一个全新已迁移库，返回 (db_path, 该库所在目录)。"""
    tmp = Path(tempfile.mkdtemp(prefix=f"novelos_probe_b_{tag}_"))
    settings = Settings(data_dir=tmp, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path), tmp


def verify_worktree_import() -> None:
    """自检：packages 必须从本工作树解析（否则探针结论对错误的树成立）。"""
    import packages.core.config as cfg

    cfg_file = Path(cfg.__file__ or "")
    assert cfg_file.is_relative_to(REPO_ROOT), (
        f"packages 解析到了工作树之外：{cfg_file}（期望在 {REPO_ROOT} 下）"
    )


def force_utf8_stdout() -> None:
    """GBK 控制台防乱码（AGENTS 坑区同族）：函数内 reconfigure + hasattr 守卫
    （批次十六纪律：pytest 捕获期 sys.stdout 没有 reconfigure ⇒ 不放模块级）。"""
    import sys as _sys

    for stream in (_sys.stdout, _sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


class Recorder:
    """逐条记录结论；结束后统一打印汇总。"""

    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, str]] = []

    def confirm(self, item: str, evidence: str) -> None:
        self.rows.append((item, "确认", evidence))
        print(f"  [确认] {item}\n         证据：{evidence}")

    def refute(self, item: str, evidence: str) -> None:
        self.rows.append((item, "推翻", evidence))
        print(f"  [推翻] {item}\n         证据：{evidence}")

    def undecidable(self, item: str, evidence: str) -> None:
        self.rows.append((item, "不可判", evidence))
        print(f"  [不可判] {item}\n         证据：{evidence}")

    def summary(self) -> int:
        n_confirm = sum(1 for r in self.rows if r[1] == "确认")
        n_refute = sum(1 for r in self.rows if r[1] == "推翻")
        n_undec = sum(1 for r in self.rows if r[1] == "不可判")
        print(
            f"\n== {self.title} 汇总：确认 {n_confirm} / 推翻 {n_refute} / 不可判 {n_undec} =="
        )
        return 0 if n_undec == 0 else 2


def insert_chapter(db_path: str, project_id: str, number: int = 1,
                   status: str = "REVIEWED") -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C1', '{}', ?, 'VISIBLE', NULL, ?, ?)",
            (cid, project_id, number, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def insert_review_run(db_path: str, chapter_id: str, *, status: str,
                      draft_version: int, started_at: str) -> str:
    """插一条 chapter-review run，checkpoint 带该次评审的 draft_version。"""
    run_id, wf_id = new_id("wfr"), new_id("wf")
    now = now_iso()
    checkpoint = {
        "author_review": {
            "__pause_payload__": {
                "stage": "chapter-review",
                "review_report": {"draft_version": draft_version, "word_count": 100},
            }
        }
    }
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'chapter-review', 'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "current_node, checkpoint_json, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, 'author_review', ?, ?, ?)",
            (run_id, wf_id, chapter_id, status,
             json.dumps(checkpoint, ensure_ascii=False), started_at, started_at),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id
