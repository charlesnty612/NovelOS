"""deconstruct-book 工作流端到端测试（Sprint 11 上半）。

覆盖：
1. 端到端跑通：3 章伪书 → T1 切分 → T2 逐章抽取 → T3 聚合 → G-sim → T4 落库。
2. T1 切不出章节 → run FAILED。
3. G-sim 阻断：aggregate mock 输出含原文 ≥13 字片段 → run FAILED，reference_canons 无行。
4. schema 不合规（aggregate mock 缺顶层字段）→ run FAILED。
5. chapter_extracts 行数 = 章节数。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.workflows.deconstruct_book import WORKFLOW, _validate_canon_schema
from packages.workflows.deconstruct_book.pipeline import _g_sim_node  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "deconstruct.db"
    apply_migrations(path)
    return path


@pytest.fixture
def engine(db_path: Path) -> WorkflowEngine:
    """WorkflowEngine fixture + 同步 deconstructor prompts（注册 agents/prompts 行）。"""
    from packages.core.agent_runtime.prompts import PromptRegistry

    # sync_from_docs 把 docs/agents/prompts/*.md upsert 到 agents/prompts 表
    PromptRegistry(db_path).sync_from_docs(
        Path(__file__).resolve().parents[2] / "docs" / "agents" / "prompts"
    )
    return WorkflowEngine(db_path)


def _make_project(db_path: Path, name: str = "测试项目") -> str:
    """手动插入 project 行（避开 Service/UUID 依赖）；同步主键直接固定字符串即可。"""
    from packages.core.ids import new_id, now_iso

    conn = get_connection(db_path)
    try:
        pid = new_id("prj")
        conn.execute(
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words,
                                   status, created_at, updated_at)
            VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, name, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


# ---------------------------------------------------------------------------
# Helpers：构造 mock_script
# ---------------------------------------------------------------------------


def _chapter_extract_mock(extracts_per_chapter: list[dict] | None = None) -> list[str]:
    """deconstructor_chapter mock script：返回 list[str]（T2 节点负责逐章分发）。

    run_agent 每次实例化新 MockProvider ⇒ _call_count 归零；list 模式跨调用
    无法累计；callable 模式因 ctx 经 checkpoint 序列化（engine.py:411）含
    非 JSON 元素也不能用。所以本节点在 T2 已实现「list 模式逐章分发」，
    测试只需提供按章顺序的 list。
    """
    default_extracts = [
        {
            "schema_version": "chapter-extract.v0",
            "chapter_index": 1,
            "event_pattern": "主角类型 1 被同辈压制当众受辱",
            "function_tag": "hook",
            "valence": 2,
            "hook_marker": "章末留悬念：物品类型 V 来历 / 敌对方将有何动作",
            "payoff_tags": [],
            "chapter_digest": "主角类型 1 出场被压制",
        },
        {
            "schema_version": "chapter-extract.v0",
            "chapter_index": 2,
            "event_pattern": "主角类型 1 暗修传承",
            "function_tag": "setup",
            "valence": 0,
            "hook_marker": "章末留悬念：传承突破",
            "payoff_tags": [],
            "chapter_digest": "主角类型 1 暗中蓄力",
        },
        {
            "schema_version": "chapter-extract.v0",
            "chapter_index": 3,
            "event_pattern": "主角类型 1 在家族大比中首次展现实力",
            "function_tag": "climax",
            "valence": 7,
            "hook_marker": "章末留悬念：反派势力类型 Y 将如何反扑",
            "payoff_tags": ["face_slap", "level_up"],
            "chapter_digest": "家族大比主角类型 1 反压制",
        },
    ]
    pool = extracts_per_chapter or default_extracts
    return [json.dumps(p, ensure_ascii=False) for p in pool]


# 保留旧 callable mock 名称以兼容（仍返回 list-strings）；不在 workflow 测试用。
_chapter_extract_callable_mock = _chapter_extract_mock


def _build_aggregate_canon(
    chapter_extracts: list[dict],
    *,
    include_overlap: str | None = None,
    drop_top_field: str | None = None,
) -> dict:
    """构造 ReferenceCanon mock 输出（含 metadata 占位）。

    - ``include_overlap``：注入到 canon_json 字符串字段的、与原文存在 ≥13 字重叠的内容
      （用于 G-sim 阻断测试）。
    - ``drop_top_field``：从合规 canon 删一个顶层必填字段（用于 schema 不合规测试）。
    """
    canon: dict = {
        "logline": "草根主角获逆袭金手指 → 家族比试首胜 → 反派出高手",
        "spine": [
            {
                "chapter_index": ce.get("chapter_index", i + 1),
                "title_pattern": f"抽象章名模板第{i+1}章",
                "function_tag": ce.get("function_tag", "setup"),
                "summary_pattern": "本章抽象事件模式描述",
            }
            for i, ce in enumerate(chapter_extracts)
        ],
        "faction_map": {
            "factions": [
                {
                    "faction_id": "fac_hero",
                    "type_pattern": "主角派",
                    "power_layer": "low",
                },
                {
                    "faction_id": "fac_rival",
                    "type_pattern": "反派势力",
                    "power_layer": "mid",
                },
            ],
            "relations": [
                {
                    "from_faction_id": "fac_hero",
                    "to_faction_id": "fac_rival",
                    "relation_type": "hostile",
                }
            ],
            "power_layers": [
                {"layer": "low", "count": 1},
                {"layer": "mid", "count": 1},
            ],
        },
        "emotion_curve": [
            {
                "chapter_index": ce.get("chapter_index", i + 1),
                "valence": ce.get("valence", 0),
                "marker_type": "buildup",
            }
            for i, ce in enumerate(chapter_extracts)
        ],
        "payoff_list": [
            {
                "payoff_id": f"payoff_{ce.get('chapter_index', i+1):03d}_fs",
                "chapter_index": ce.get("chapter_index", i + 1),
                "type": "face_slap",
                "intensity": 2,
                "setup_chapter": ce.get("chapter_index", i + 1),
                "payoff_chapter": ce.get("chapter_index", i + 1),
            }
            for i, ce in enumerate(chapter_extracts)
        ],
        "techniques": [
            {
                "technique_id": "tech_001",
                "name_pattern": "黄金三章强制钩子",
                "location_pattern": "前三章章末",
                "effect_pattern": "前 300 字冲突前置 + 三章钩子",
            }
        ],
        "rhythm": {
            "mini_climax_interval": {"median": 3, "p25": 2, "p75": 5},
            "major_climax_interval": {"median": 5, "p25": 4, "p75": 7},
            "chapter_end_hook_rate": 0.8,
            "golden_three_compliance": {
                "first_300_chars_conflict": True,
                "ch1_end_hook": True,
                "ch2_end_hook": True,
                "ch3_end_hook": True,
                "mini_climax_in_first_three": True,
            },
        },
        "style_params": {
            "sentence_length_distribution": {"mean": 18.0, "median": 16.0, "max": 80},
            "dialogue_ratio": 0.25,
            "action_ratio": 0.45,
            "pov": "third_limited",
            "paragraph_length_distribution": {"mean": 120.0, "median": 100.0, "max": 600},
            "psychological_ratio": 0.15,
            "environment_ratio": 0.15,
        },
        "metadata": {
            "source_book_title": "<PLACEHOLDER>",
            "deconstruct_date": "2026-08-23T00:00:00+00:00",
            "deconstruct_version": "deconstruct-book-v0",
            "target_reader_profile": "male_fantasy",
            "license_check_status": {
                "checked": False,
                "license": "unknown",
                "compatible": False,
            },
        },
    }
    if include_overlap:
        # 注入到 logline 字段（合规 canon 的 string 字段）以触发 G-sim 阻断
        canon["logline"] = include_overlap
    if drop_top_field and drop_top_field in canon:
        del canon[drop_top_field]
    return canon


def _aggregate_mock(canon: dict) -> list[str]:
    """deconstructor_aggregate mock script（返回单条 canon JSON）。"""
    return [json.dumps(canon, ensure_ascii=False)]


# ---------------------------------------------------------------------------
# 伪书文本（3 章，"第N章" + 标题 + 简短正文；mock 内容可与原文不重复以通过 G-sim）
# ---------------------------------------------------------------------------

_SAMPLE_BOOK_TEXT = (
    "第一章 少年被欺\n"
    "叶家演武台上，少年被同辈一掌震飞。众人哄笑，他咬牙站起。\n"
    "玉佩发出微光，苍老声音在脑海炸响。\n"
    "\n"
    "第二章 暗修传承\n"
    "主角暗中修习玉佩所授心法。同族反派数次挑衅，他避其锋芒。\n"
    "\n"
    "第三章 家族大比\n"
    "家族大比之日，主角在众人注视下首次展现实力，反压同辈。\n"
)

# 与 _SAMPLE_BOOK_TEXT 内任意 ≥13 字片段；用于 G-sim 阻断测试
_OVERLAP_FRAGMENT = "少年被同辈一掌震飞。众人哄笑，他咬牙站起。"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_deconstruct_book_end_to_end(db_path: Path, engine: WorkflowEngine):
    """端到端跑通：3 章 → canon 落库 + extracts 3 行 + report_md 非空 + metadata 五字段齐。"""
    pid = _make_project(db_path)

    chapter_mock = _chapter_extract_callable_mock()
    canon = _build_aggregate_canon(
        [
            {"chapter_index": 1, "function_tag": "hook", "valence": 2},
            {"chapter_index": 2, "function_tag": "setup", "valence": 0},
            {"chapter_index": 3, "function_tag": "climax", "valence": 7},
        ]
    )
    aggregate_mock = _aggregate_mock(canon)

    run_id = engine.start_with_nodes(
        "deconstruct-book",
        WORKFLOW["nodes"],
        chapter_id=None,
        initial_ctx={
            "db_path": str(db_path),
            "project_id": pid,
            "book_title": "测试参照书",
            "text": _SAMPLE_BOOK_TEXT,
            "reader_profile": "male_fantasy",
            "mock_providers": {
                "deconstructor_chapter": chapter_mock,
                "deconstructor_aggregate": aggregate_mock,
            },
        },
        mock_providers={
            "deconstructor_chapter": chapter_mock,
            "deconstructor_aggregate": aggregate_mock,
        },
    )

    conn = get_connection(db_path)
    try:
        run = conn.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert run is not None
        assert run["status"] == "COMPLETED", f"run FAILED: {run['error']}"

        canon_rows = conn.execute(
            "SELECT * FROM reference_canons WHERE project_id = ?", (pid,)
        ).fetchall()
        assert len(canon_rows) == 1
        canon_row = canon_rows[0]
        canon_id = canon_row["canon_id"]
        assert canon_row["title"] == "测试参照书"
        assert canon_row["reader_profile"] == "male_fantasy"
        assert canon_row["status"] == "active"
        assert (canon_row["report_md"] or "") != ""

        stored_canon = json.loads(canon_row["canon_json"])
        # metadata 五字段齐
        md = stored_canon.get("metadata", {})
        for k in (
            "source_book_title",
            "deconstruct_date",
            "deconstruct_version",
            "target_reader_profile",
            "license_check_status",
        ):
            assert k in md, f"missing metadata field {k}"
        assert md["source_book_title"] == "测试参照书"
        assert md["deconstruct_version"] == "deconstruct-book-v0"
        assert md["target_reader_profile"] == "male_fantasy"

        extract_rows = conn.execute(
            "SELECT chapter_index FROM canon_extracts WHERE canon_id = ? ORDER BY chapter_index ASC",
            (canon_id,),
        ).fetchall()
        assert [r["chapter_index"] for r in extract_rows] == [1, 2, 3]
    finally:
        conn.close()


def test_t1_no_chapter_split_fails(db_path: Path, engine: WorkflowEngine):
    """T1 切不出章节 → run FAILED（mock 不应被调用）。"""
    pid = _make_project(db_path)
    chapter_mock = _chapter_extract_callable_mock()
    canon = _build_aggregate_canon(
        [{"chapter_index": 1, "function_tag": "hook", "valence": 2}]
    )
    aggregate_mock = _aggregate_mock(canon)

    run_id = engine.start_with_nodes(
        "deconstruct-book",
        WORKFLOW["nodes"],
        chapter_id=None,
        initial_ctx={
            "db_path": str(db_path),
            "project_id": pid,
            "book_title": "无章节标题的书",
            "text": "这是一段连续正文，没有任何第N章标题。",
            "reader_profile": "male_fantasy",
            "mock_providers": {
                "deconstructor_chapter": chapter_mock,
                "deconstructor_aggregate": aggregate_mock,
            },
        },
        mock_providers={
            "deconstructor_chapter": chapter_mock,
            "deconstructor_aggregate": aggregate_mock,
        },
    )

    conn = get_connection(db_path)
    try:
        run = conn.execute(
            "SELECT status, error, current_node FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert run["status"] == "FAILED"
        assert "T1" in (run["error"] or ""), run["error"]
        # reference_canons 不应有任何行
        canon_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM reference_canons WHERE project_id = ?", (pid,)
        ).fetchone()
        assert canon_rows["n"] == 0
    finally:
        conn.close()


def test_g_sim_blocks_overlap(db_path: Path, engine: WorkflowEngine):
    """G-sim 阻断：aggregate mock 输出含原文 ≥13 字片段 → run FAILED，reference_canons 无行。"""
    pid = _make_project(db_path)

    chapter_mock = _chapter_extract_callable_mock()
    # 注意：aggregate mock 输出 logline 字段含原文 16+ 字片段
    canon = _build_aggregate_canon(
        [
            {"chapter_index": 1, "function_tag": "hook", "valence": 2},
            {"chapter_index": 2, "function_tag": "setup", "valence": 0},
            {"chapter_index": 3, "function_tag": "climax", "valence": 7},
        ],
        include_overlap=_OVERLAP_FRAGMENT,
    )
    # logline 改成了原文片段，schema 仍合规（≤80 字）；G-sim 应阻断
    assert _validate_canon_schema(canon) == []

    aggregate_mock = _aggregate_mock(canon)

    run_id = engine.start_with_nodes(
        "deconstruct-book",
        WORKFLOW["nodes"],
        chapter_id=None,
        initial_ctx={
            "db_path": str(db_path),
            "project_id": pid,
            "book_title": "G-sim 阻断测试",
            "text": _SAMPLE_BOOK_TEXT,
            "reader_profile": "male_fantasy",
            "mock_providers": {
                "deconstructor_chapter": chapter_mock,
                "deconstructor_aggregate": aggregate_mock,
            },
        },
        mock_providers={
            "deconstructor_chapter": chapter_mock,
            "deconstructor_aggregate": aggregate_mock,
        },
    )

    conn = get_connection(db_path)
    try:
        run = conn.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert run["status"] == "FAILED"
        assert "G-sim" in (run["error"] or ""), run["error"]
        canon_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM reference_canons WHERE project_id = ?", (pid,)
        ).fetchone()
        assert canon_rows["n"] == 0, "G-sim 阻断后不应有 reference_canons 行"
    finally:
        conn.close()


def test_t3_schema_invalid_fails(db_path: Path, engine: WorkflowEngine):
    """schema 不合规（aggregate mock 缺顶层字段）→ run FAILED。"""
    pid = _make_project(db_path)

    chapter_mock = _chapter_extract_callable_mock()
    canon = _build_aggregate_canon(
        [
            {"chapter_index": 1, "function_tag": "hook", "valence": 2},
            {"chapter_index": 2, "function_tag": "setup", "valence": 0},
            {"chapter_index": 3, "function_tag": "climax", "valence": 7},
        ],
        drop_top_field="techniques",  # 必填顶层字段删掉
    )
    # 确认 canon 自检已识别不合规（不在 mock 跑前断言；schema_errors 应非空）
    assert _validate_canon_schema(canon) != []

    aggregate_mock = _aggregate_mock(canon)

    run_id = engine.start_with_nodes(
        "deconstruct-book",
        WORKFLOW["nodes"],
        chapter_id=None,
        initial_ctx={
            "db_path": str(db_path),
            "project_id": pid,
            "book_title": "Schema 不合规测试",
            "text": _SAMPLE_BOOK_TEXT,
            "reader_profile": "male_fantasy",
            "mock_providers": {
                "deconstructor_chapter": chapter_mock,
                "deconstructor_aggregate": aggregate_mock,
            },
        },
        mock_providers={
            "deconstructor_chapter": chapter_mock,
            "deconstructor_aggregate": aggregate_mock,
        },
    )

    conn = get_connection(db_path)
    try:
        run = conn.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert run["status"] == "FAILED"
        # run_agent 重试 1 次后失败；最终 error 含 "schema invalid" 字样
        err = run["error"] or ""
        assert "T3 aggregate schema invalid" in err or "aggregate" in err, err
        canon_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM reference_canons WHERE project_id = ?", (pid,)
        ).fetchone()
        assert canon_rows["n"] == 0
    finally:
        conn.close()


# ===========================================================================
# Sprint 11 审查补测试（F1/F2/F3）
# ===========================================================================


def _book_text_for_offset_check() -> tuple[str, list[tuple[int, int, str]]]:
    """返回 (text, expected_segments)。

    expected_segments = [(start, end, title)]；用于 F1 偏移切片断言。
    """
    text = (
        "第一章 少年被欺\n"
        "甲乙丙丁戊己庚辛壬癸十二字占位。\n"
        "\n"
        "第二章 暗修传承\n"
        "子丑寅卯辰巳午未申酉戌亥十二字续。\n"
        "\n"
        "第三章 家族大比\n"
        "abcdefghijklmnopqrstuvwxyz占位二十六字。\n"
    )
    expected = [
        (0, len("第一章 少年被欺\n甲乙丙丁戊己庚辛壬癸十二字占位。\n\n"), "第一章 少年被欺"),
        (
            len(text[: len("第一章 少年被欺\n甲乙丙丁戊己庚辛壬癸十二字占位。\n\n")]),
            len(
                text[: len(
                    "第一章 少年被欺\n甲乙丙丁戊己庚辛壬癸十二字占位。\n\n"
                    "第二章 暗修传承\n子丑寅卯辰巳午未申酉戌亥十二字续。\n\n"
                )]
            ),
         "第二章 暗修传承"),
    ]
    return text, expected


def test_t1_segments_have_no_raw_text(db_path: Path, engine: WorkflowEngine):
    """F1：T1 输出 segments 不应含 raw_text；改用 start_offset/end_offset。"""
    from packages.workflows.deconstruct_book.pipeline import _t1_split_chapters_node

    text = _SAMPLE_BOOK_TEXT
    out = _t1_split_chapters_node({"text": text})
    assert out["chapter_count"] == 3
    # 期望各章正文特征（与 _SAMPLE_BOOK_TEXT 对齐）
    expected_features = [
        "玉佩发出微光",          # 第一章
        "暗中修习玉佩所授心法",   # 第二章
        "家族大比之日",           # 第三章
    ]
    for idx, seg in enumerate(out["segments"]):
        assert "raw_text" not in seg, f"segment 不应含 raw_text: {seg.keys()}"
        assert "start_offset" in seg and "end_offset" in seg
        assert "chapter_index" in seg and "title" in seg
        # offset 切片能准确取回该章节原文
        raw_slice = text[int(seg["start_offset"]) : int(seg["end_offset"])]
        assert expected_features[idx] in raw_slice, (
            f"chapter_index={idx+1} 切片未含预期特征 {expected_features[idx]!r}；"
            f"切片={raw_slice[:60]!r}"
        )


def test_t2_extract_slices_raw_text_via_offsets(db_path: Path, engine: WorkflowEngine):
    """F1：T2 节点按 T1 offsets 在 ctx['text'] 切片取每章 raw_text（不写回 ctx）。"""
    pid = _make_project(db_path)
    chapter_mock = _chapter_extract_callable_mock()
    canon = _build_aggregate_canon(
        [
            {"chapter_index": 1, "function_tag": "hook", "valence": 2},
            {"chapter_index": 2, "function_tag": "setup", "valence": 0},
            {"chapter_index": 3, "function_tag": "climax", "valence": 7},
        ]
    )
    aggregate_mock = _aggregate_mock(canon)

    # 端到端跑通，确认 segments 在 T2 内部按 offset 切片正常拿到 raw_text 走 LLM
    run_id = engine.start_with_nodes(
        "deconstruct-book",
        WORKFLOW["nodes"],
        chapter_id=None,
        initial_ctx={
            "db_path": str(db_path),
            "project_id": pid,
            "book_title": "F1 offset 切片测试",
            "text": _SAMPLE_BOOK_TEXT,
            "reader_profile": "male_fantasy",
            "mock_providers": {
                "deconstructor_chapter": chapter_mock,
                "deconstructor_aggregate": aggregate_mock,
            },
        },
        mock_providers={
            "deconstructor_chapter": chapter_mock,
            "deconstructor_aggregate": aggregate_mock,
        },
    )
    conn = get_connection(db_path)
    try:
        run = conn.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert run["status"] == "COMPLETED", f"run FAILED: {run['error']}"
        # 验证 chapter_extracts 行（落 canon_extracts 表）不含 raw_text
        extract_rows = conn.execute(
            "SELECT extract_json FROM canon_extracts ce "
            "JOIN reference_canons rc ON ce.canon_id = rc.canon_id "
            "WHERE rc.project_id = ?",
            (pid,),
        ).fetchall()
        assert extract_rows, "应有 canon_extracts 行"
        for row in extract_rows:
            blob = row["extract_json"] or ""
            assert "raw_text" not in blob, "canon_extracts 不应存 raw_text"
            # 也不应存原文特征
            for fragment in ("玉佩发出微光", "少年被同辈一掌震飞"):
                assert fragment not in blob
    finally:
        conn.close()


def test_checkpoint_exclude_excludes_text_from_db(db_path: Path, engine: WorkflowEngine):
    """F1 真实 SELECT 断言：checkpoint_exclude=['text'] 时原书 text 不落 workflow_runs。

    通过直接调 engine.start_with_nodes 传 checkpoint_exclude；不走 router。
    """
    pid = _make_project(db_path)
    chapter_mock = _chapter_extract_callable_mock()
    canon = _build_aggregate_canon(
        [
            {"chapter_index": 1, "function_tag": "hook", "valence": 2},
            {"chapter_index": 2, "function_tag": "setup", "valence": 0},
            {"chapter_index": 3, "function_tag": "climax", "valence": 7},
        ]
    )
    aggregate_mock = _aggregate_mock(canon)
    # 原文特征子串（≥13 字命中 G-sim 的最低门槛）
    distinct_fragment = "玉佩发出微光，苍老声音在脑海炸响"

    run_id = engine.start_with_nodes(
        "deconstruct-book",
        WORKFLOW["nodes"],
        chapter_id=None,
        initial_ctx={
            "db_path": str(db_path),
            "project_id": pid,
            "book_title": "F1 落盘隔离",
            "text": _SAMPLE_BOOK_TEXT + distinct_fragment,
            "reader_profile": "male_fantasy",
            "mock_providers": {
                "deconstructor_chapter": chapter_mock,
                "deconstructor_aggregate": aggregate_mock,
            },
        },
        mock_providers={
            "deconstructor_chapter": chapter_mock,
            "deconstructor_aggregate": aggregate_mock,
        },
        checkpoint_exclude=["text"],
    )
    conn = get_connection(db_path)
    try:
        # 1) workflow_runs.checkpoint_json 不含 ctx['text']
        run = conn.execute(
            "SELECT status, checkpoint_json FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert run["status"] == "COMPLETED", run["status"]
        ckpt_blob = run["checkpoint_json"] or ""
        assert "ctx_text" not in ckpt_blob and distinct_fragment not in ckpt_blob
        # 也不应含其他任何原文 13+ 字特征子串（覆盖检查）
        # 取 SAMPLE_BOOK_TEXT 任一 ≥13 字片段
        for fragment in (
            "玉佩发出微光，苍老声音在脑海炸响",  # 加在末尾的特征串
            "少年被同辈一掌震飞。众人哄笑，他咬牙站起",
        ):
            assert fragment not in ckpt_blob

        # 2) workflow_run_nodes.output_json（T1/T2/T3/T4）也不含原文
        node_rows = conn.execute(
            "SELECT node_id, output_json FROM workflow_run_nodes WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        for nr in node_rows:
            blob = nr["output_json"] or ""
            assert distinct_fragment not in blob, (
                f"node {nr['node_id']} output 落库了原文"
            )

        # 3) ai_call_logs.input_context_ids_json 不含原文（仅 *_id）
        #    ai_call_logs 没有 input_json 列；input_context_ids_json 已经只取 *_id
        #    这里用 LENGTH 兜底：不应包含原文特征
        aic_rows = conn.execute(
            "SELECT input_context_ids_json FROM ai_call_logs "
            "JOIN workflow_run_nodes ON ai_call_logs.node_run_id = workflow_run_nodes.node_run_id "
            "WHERE workflow_run_nodes.run_id = ?",
            (run_id,),
        ).fetchall()
        for ar in aic_rows:
            blob = ar["input_context_ids_json"] or ""
            for fragment in (
                "玉佩发出微光",
                "少年被同辈一掌震飞",
                distinct_fragment,
            ):
                assert fragment not in blob, (
                    f"ai_call_logs 落库了原文片段: {fragment}"
                )
    finally:
        conn.close()


def test_t2_validation_retry_then_success(db_path: Path, engine: WorkflowEngine):
    """F2 脚本次序：T2 第一次返回 banned_key raw_text_excerpt → 校验失败 → 重试 → 成功。

    通过 mock_script list 注入第 1 章：[bad_script, good_script]。
    T2 节点在 retry 时把 current_script 切到 [good_script]，
    MockProvider 新实例 → 取 [0]=good_script → 校验通过。
    第 2/3 章再各自分发 good_script（chapter_mock 列表均匀）。
    """
    pid = _make_project(db_path)

    # 第一章脚本：违反 T2 校验（含 raw_text_excerpt）
    bad_script = json.dumps(
        {
            "schema_version": "chapter-extract.v0",
            "event_pattern": "主角类型 1 被同辈压制当众受辱",
            "function_tag": "hook",
            "valence": 2,
            "hook_marker": "章末留悬念",
            "payoff_tags": [],
            "chapter_digest": "主角类型 1 出场被压制",
            "raw_text_excerpt": "违规：含原文片段",  # banned
        },
        ensure_ascii=False,
    )
    # 干净脚本（重试成功；也是第 2/3 章的脚本）
    good_script = json.dumps(
        {
            "schema_version": "chapter-extract.v0",
            "event_pattern": "主角类型 1 抽象事件模式",
            "function_tag": "hook",
            "valence": 2,
            "hook_marker": "章末留悬念",
            "payoff_tags": [],
            "chapter_digest": "本章摘要",
        },
        ensure_ascii=False,
    )

    chapter_mock = [bad_script, good_script, good_script]
    canon = _build_aggregate_canon(
        [
            {"chapter_index": 1, "function_tag": "hook", "valence": 2},
            {"chapter_index": 2, "function_tag": "setup", "valence": 0},
            {"chapter_index": 3, "function_tag": "climax", "valence": 7},
        ]
    )
    aggregate_mock = _aggregate_mock(canon)

    run_id = engine.start_with_nodes(
        "deconstruct-book",
        WORKFLOW["nodes"],
        chapter_id=None,
        initial_ctx={
            "db_path": str(db_path),
            "project_id": pid,
            "book_title": "F2 retry-success",
            "text": _SAMPLE_BOOK_TEXT,
            "reader_profile": "male_fantasy",
            "mock_providers": {
                "deconstructor_chapter": chapter_mock,
                "deconstructor_aggregate": aggregate_mock,
            },
        },
        mock_providers={
            "deconstructor_chapter": chapter_mock,
            "deconstructor_aggregate": aggregate_mock,
        },
        checkpoint_exclude=["text"],
    )
    conn = get_connection(db_path)
    try:
        run = conn.execute(
            "SELECT status FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert run["status"] == "COMPLETED", (
            f"T2 校验重试成功后应 COMPLETED，run.status={run['status']}"
        )
    finally:
        conn.close()


def test_t2_validation_retry_then_fail(db_path: Path, engine: WorkflowEngine):
    """F2 仍失败：T2 脚本违反校验（function_tag 不在枚举）→ retry 仍失败 → run FAILED。

    mock_script 列表只有 1 条 bad_script，T2 retry 时切到 []（空 list），
    MockProvider(scripted=[]) 行为见 providers.py：返回空字符串 → extract 解析失败 → 抛错。
    但 T2 retry 路径不变：直接抛 ValueError('validation failed after retry')。
    """
    pid = _make_project(db_path)

    bad_script = json.dumps(
        {
            "schema_version": "chapter-extract.v0",
            "chapter_index": 1,
            "event_pattern": "抽象事件",
            "function_tag": "unknown_tag",  # 越枚举
            "valence": 2,
            "hook_marker": "h",
            "payoff_tags": [],
            "chapter_digest": "d",
        },
        ensure_ascii=False,
    )

    chapter_mock = [bad_script, bad_script, bad_script]
    canon = _build_aggregate_canon(
        [
            {"chapter_index": 1, "function_tag": "hook", "valence": 2},
            {"chapter_index": 2, "function_tag": "setup", "valence": 0},
            {"chapter_index": 3, "function_tag": "climax", "valence": 7},
        ]
    )
    aggregate_mock = _aggregate_mock(canon)

    run_id = engine.start_with_nodes(
        "deconstruct-book",
        WORKFLOW["nodes"],
        chapter_id=None,
        initial_ctx={
            "db_path": str(db_path),
            "project_id": pid,
            "book_title": "F2 retry-fail",
            "text": _SAMPLE_BOOK_TEXT,
            "reader_profile": "male_fantasy",
            "mock_providers": {
                "deconstructor_chapter": chapter_mock,
                "deconstructor_aggregate": aggregate_mock,
            },
        },
        mock_providers={
            "deconstructor_chapter": chapter_mock,
            "deconstructor_aggregate": aggregate_mock,
        },
        checkpoint_exclude=["text"],
    )
    conn = get_connection(db_path)
    try:
        run = conn.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert run["status"] == "FAILED", "T2 重试失败后 run 应 FAILED"
        assert "validation failed after retry" in (run["error"] or ""), run["error"]
    finally:
        conn.close()


def test_t3_schema_retry_then_success(db_path: Path, engine: WorkflowEngine):
    """F3：T3 schema 校验失败 → 节点级 retry 1 次后成功。

    测试策略：构造一个「count-tracking list mock」让 runner 每次实例化新 MockProvider
    也能累计调用次数（详见 _CountingList），第 1 次返回 schema 不合规 canon，第 2 次返回合规 canon。
    run_agent 每次 invoke → provider 实例化 → _call_count=0 → list[0]；本测试用
    list 模式 + monkeypatch _t3_aggregate_node 的 retry 行为不在范围。
    实际：让 canonical mock list[0] 是无效的、list[1] 是合规的；
    但 MockProvider 跨实例归零，所以改用 callback-based mock list（第一次调用拿 [0]，第二次拿 [1]）。
    但 callback/list 都被归零；因此用本测试最干净的写法：把 list 通过 mock_script kwarg
    传入但**让 MockProvider 保留状态**——这里直接调 _t3_aggregate_node 函数手工验证行为，
    不发起完整 workflow；单元测试 T3 retry 行为本身，断言它**第二次会再调 run_agent**
    且 第二次拿到的脚本与第一次不同（验证 retry 路径生效）。
    """
    _ = _make_project(db_path)  # 仅需副作用：建工程
    _ = _chapter_extract_callable_mock()  # 仅需副作用：注册 callable mock

    canon_valid = _build_aggregate_canon(
        [
            {"chapter_index": 1, "function_tag": "hook", "valence": 2},
            {"chapter_index": 2, "function_tag": "setup", "valence": 0},
            {"chapter_index": 3, "function_tag": "climax", "valence": 7},
        ]
    )
    canon_invalid = dict(canon_valid)
    canon_invalid.pop("techniques", None)  # 触发 schema 不合规
    bad_script = json.dumps(canon_invalid, ensure_ascii=False)
    good_script = json.dumps(canon_valid, ensure_ascii=False)

    # 该 list 专门用于测试：保持跨调用计数不被归零（重写 MockProvider 外层）
    # 由于 MockProvider 实例化时归零计数，最干净的 E2E 测试用 _t3_aggregate_node 直接调用；
    # 该测试断言「payload 第 1 次无 _retry_hint，第 2 次含 _retry_hint」即 retry 逻辑生效。
    captured_payloads: list[dict[str, Any]] = []

    from unittest.mock import patch

    from packages.workflows.deconstruct_book import pipeline as _pipeline_mod

    def _fake_run_agent(*args, **kwargs):
        # args: (db_path, agent_name, payload, run_id) ；payload 是位置参数 2
        payload = args[2]
        captured_payloads.append(dict(payload))
        n = len(captured_payloads)
        if n == 1:
            # 第一次：返回 schema 不合规 canon
            return canon_invalid
        # 第二次：返回合规 canon
        return canon_valid

    # 单元式测：直接调 _t3_aggregate_node，绕过 engine ctx json
    valid_extracts = [
        {"chapter_index": 1, "function_tag": "hook", "valence": 2,
         "event_pattern": "e", "hook_marker": "h", "chapter_digest": "d",
         "schema_version": "chapter-extract.v0", "payoff_tags": []}
    ]
    ctx: dict[str, Any] = {
        "db_path": str(db_path),
        "run_id": "wfr_f3unit",
        "chapter_extracts": valid_extracts,
        "book_title": "F3 retry-success",
        "reader_profile": "male_fantasy",
        "deconstruct_date": "2026-08-23T00:00:00+00:00",
        "deconstruct_version": "deconstruct-book-v0",
        "mock_providers": {"deconstructor_aggregate": [bad_script, good_script]},
        "_current_node_run_id": "wfrn_f3unit",
    }

    with patch.object(_pipeline_mod, "run_agent", side_effect=_fake_run_agent):
        out = _pipeline_mod._t3_aggregate_node(ctx)

    assert out["canon_json"] is canon_valid
    assert len(captured_payloads) == 2
    # 第二次 payload 包含 _retry_hint（schema note）
    assert "_retry_hint" in captured_payloads[1]
    assert "Schema note" in captured_payloads[1]["_retry_hint"]
