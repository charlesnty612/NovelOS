"""A-001/A-002/B-6 整改：prompt 版本标签单点化（查 DB ACTIVE）+ director 缓存键补维度。

缺陷形状（``prompts.py:314-324`` 记载的 2026-09-16 writer 事故，第二次在 director /
observer / director_planner 重现）：payload 声明的 ``prompt_version`` 是**硬编码字面量**，
提示词一升版就漂移——payload 声明旧版、模型照抄回声、落库记旧版，而
``ai_call_logs`` 记真值，同一条链两个值且无任何报错。

两条独立断言线（缺一不可）：
1. **payload 跟随 ACTIVE**：升版后 payload 必须声明新版本（否则声明与实际脱节）；
2. **缓存键补维度**：director 键必须含 prompt 标签，否则同 state_version 下升版会脏命中
   旧条目（读到的 payload 声明新版本、内容却是旧装配）。
   ⚠️ 两者必须同批改：只改 payload 不改键反而会**制造**真脏命中。

全部用 tmp_path 临时库 + 合成数据，不接触 dev 库、不发起任何模型调用。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.agent_runtime.prompts import PromptRegistry
from packages.core.context_engine.builders import _cache_reset, build_director_input
from packages.core.context_engine.observer_input import build_observer_input
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

DIRECTOR_KEY_LEN = 18  # 12 → 13：新增 prompt_label 维度；13 → 14：新增 chars_fp 角色维度
#                       （E1，2026-10-16）；14 → 15：新增 skills_fp 绑定 skill 集合维度
#                       （0032 内部消费外部 skill）；15 → 16：新增 world_fp 世界观三表
#                       内容指纹（P1-3，2026-10-21；键尾两元恒为 prompt_label + ns_tag）；
#                       16 → 17：新增 project_meta_fp 项目 name/genre/premise 维度
#                       （P0-1-A，2026-10-22 批次二十一）；17 → 18：新增 volume_scope
#                       所属卷维度（P0-1-C，2026-10-23：assign_chapter 不推 state_version）


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str, number: int, *, plan_json: str = "{}") -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C', ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, plan_json, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, ?, 'agent:writer:v1', 'writer:v1', 'mock/test', ?)",
            (new_id("dr"), cid, "", now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _sync_prompt(tmp_path: Path, db_path: Path, agent: str, version: int) -> None:
    """把 ``<agent>-v<N>.md`` 同步进临时库的 prompts 表（ACTIVE = 最高 N）。"""
    docs = tmp_path / f"prompts_{agent}_{version}"
    docs.mkdir(exist_ok=True)
    (docs / f"{agent}-v{version}.md").write_text(
        f"# {agent} v{version}\n", encoding="utf-8"
    )
    PromptRegistry(db_path).sync_from_docs(docs)


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    """统计 module.attr 的真实调用次数（判别缓存命中/未命中的有效信号）。"""
    counter = {"n": 0}
    real = getattr(module, attr)

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


# ---------------------------------------------------------------------------
# 1. director：payload 跟随 ACTIVE
# ---------------------------------------------------------------------------


def test_director_payload_prompt_version_follows_active(tmp_path: Path):
    """director 装配 payload 的 prompt_version 取 ACTIVE 提示词行，不再硬编码。

    未 sync 任何提示词时回落历史默认值 ``director:v1``（临时库 / 未 sync 场景）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _cache_reset()

    p0 = build_director_input(db_path, pid, cid, "意图")
    assert p0["prompt_version"] == "director:v1", "无 ACTIVE 行时应回落 fallback"

    _sync_prompt(tmp_path, db_path, "director", 2)
    _cache_reset()
    p1 = build_director_input(db_path, pid, cid, "意图")
    assert p1["prompt_version"] == "director:v2", "升版后 payload 声明必须跟随 ACTIVE"

    _sync_prompt(tmp_path, db_path, "director", 10)
    _cache_reset()
    p2 = build_director_input(db_path, pid, cid, "意图")
    assert p2["prompt_version"] == "director:v10", "ACTIVE 取最高 N（v10 > v2，数字序）"


# ---------------------------------------------------------------------------
# 2. director：缓存键补 prompt_label 维度（升版必 miss）
# ---------------------------------------------------------------------------


def test_director_cache_key_has_prompt_label_dimension(tmp_path: Path):
    """director 键 12 → 13 元，新增维度落在键尾 ns_tag 之前（位置仿 writer 键[13]）。"""
    from packages.core.context_engine import cache as cache_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _sync_prompt(tmp_path, db_path, "director", 2)
    _cache_reset()

    payload = build_director_input(db_path, pid, cid, "意图")
    keys = [k for k in cache_mod._assembly_cache if k[0] == pid and k[3] == "director"]
    assert len(keys) == 1, f"应有 1 条 director 键，实得 {len(keys)}"
    key = keys[0]
    assert len(key) == DIRECTOR_KEY_LEN, f"键应为 {DIRECTOR_KEY_LEN} 元，实得 {len(key)}"
    assert key[-2] == payload["prompt_version"] == "director:v2", (
        "倒数第二位必须是 prompt 标签且与 payload 声明同源同值"
    )
    assert key[-1] == cache_mod._cache_namespace_tag(""), "键尾恒为命名空间标记"


def test_director_cache_miss_when_prompt_version_changes(tmp_path: Path, monkeypatch):
    """升版后同 state_version 必须重装——否则读到「声明新版本、内容却是旧装配」的条目。

    这是补键维度的**唯一**理由：payload 与键不同步时，v1/v2 两次装配会落到同一键。
    """
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _sync_prompt(tmp_path, db_path, "director", 2)
    _cache_reset()

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    a1 = build_director_input(db_path, pid, cid, "意图")
    assert a1["prompt_version"] == "director:v2"
    a2 = build_director_input(db_path, pid, cid, "意图")
    assert a2["prompt_version"] == "director:v2"
    assert calls["n"] == 1, "同版本应命中缓存"

    _sync_prompt(tmp_path, db_path, "director", 3)
    a3 = build_director_input(db_path, pid, cid, "意图")
    assert a3["prompt_version"] == "director:v3"
    assert calls["n"] == 2, "提示词升版必须 miss 旧装配（payload 与键同批补维度）"


# ---------------------------------------------------------------------------
# 3. observer / director_planner：同款单点化
# ---------------------------------------------------------------------------


def test_observer_payload_prompt_version_follows_active(tmp_path: Path):
    """observer 装配 payload 的 prompt_version 取 ACTIVE（无缓存，故无键需同步）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)

    p0 = build_observer_input(db_path, cid)
    assert p0["prompt_version"] == "observer:v1", "无 ACTIVE 行时应回落 fallback"

    _sync_prompt(tmp_path, db_path, "observer", 2)
    p1 = build_observer_input(db_path, cid)
    assert p1["prompt_version"] == "observer:v2", "升版后 payload 声明必须跟随 ACTIVE"


def test_director_planner_payload_prompt_version_follows_active(tmp_path: Path):
    """director_planner 装配 payload 的 prompt_version 取 ACTIVE，常量降级为 fallback。

    planner 复用 director 键空间、键形态不动（payload 每次调用都在 :218 覆写该字段，
    故不依赖缓存条目里的值——深拷贝 + 覆写，不回写缓存）。
    """
    from packages.workflows.chapter_plan.planner_input import build_director_planner_input

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _cache_reset()

    p0 = build_director_planner_input(db_path, pid, cid, "意图")
    assert p0["prompt_version"] == "director_planner:v2", "无 ACTIVE 行时应回落常量"

    _sync_prompt(tmp_path, db_path, "director_planner", 3)
    p1 = build_director_planner_input(db_path, pid, cid, "意图")
    assert p1["prompt_version"] == "director_planner:v3", "升版后 payload 声明必须跟随 ACTIVE"


# ---------------------------------------------------------------------------
# 4. director_planner：chapter_scene_plans 落库侧同源（Wave2 CRITICAL-1 落库侧）
# ---------------------------------------------------------------------------


def _director_output_with_scene_plan(prompt_echo: str | None) -> dict:
    """合成 director_planner 输出：带非空 scene_plan（触发落库分支）。"""
    return {
        "chapter_goal": "目标",
        "expected_role": "setup",
        "expected_word_count": 1200,
        "scene_plan": {"scenes": [{"beat_id": "b1", "purpose": "开场"}]},
        # 模型回声：模型照抄 payload 的 prompt_version 后回吐，可被改写 / 省略，
        # 落库侧必须与它无关（否则升版后「装配声明新版本、落库记旧版」）。
        "prompt_version": prompt_echo,
        "schema_version": "director_plan.v1",
    }


def _run_save_plan(db_path: Path, pid: str, cid: str, prompt_echo: str | None) -> dict:
    """直接驱动 save_plan 节点（State 节点），不经过引擎、不触发任何模型调用。"""
    from packages.workflows.chapter_plan.pipeline import _save_plan_node

    return _save_plan_node(
        {
            "db_path": db_path,
            "chapter_id": cid,
            "project_id": pid,
            "run_id": new_id("wfr"),
            "director_planner_output": _director_output_with_scene_plan(prompt_echo),
        }
    )


def _plan_json_prompt_version(db_path: Path, cid: str) -> str | None:
    """读 chapters.plan_json 落库的 prompt_version（plan_json 侧落库面）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(row["plan_json"]).get("prompt_version")


def test_scene_plan_persisted_prompt_version_follows_active(tmp_path: Path):
    """两个落库面（chapters.plan_json + chapter_scene_plans）都落 **ACTIVE 真值**，
    且都不被模型回声污染。

    CRITICAL-1 的落库侧：payload 侧早已取 ACTIVE（planner_input.build_director_planner_input），
    但落库侧曾一处传硬编码常量、一处取模型回声——升版后同一 run 出现
    「装配声明 v3 / 落库记 v2（或记回声）」多个值，零报错。
    """
    from packages.workflows.chapter_plan.scene_plan_store import load_scene_plan

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    _sync_prompt(tmp_path, db_path, "director_planner", 3)

    # 回声故意污染成 v999：若任一落库面采回声，对应断言红。
    _run_save_plan(db_path, pid, cid, "director_planner:v999")

    row = load_scene_plan(db_path, cid)
    assert row is not None, "本轮产出非空 scene_plan，必须落库"
    assert row["prompt_version"] == "director_planner:v3", (
        f"chapter_scene_plans 落库值必须取 ACTIVE 真值 director_planner:v3，"
        f"实得 {row['prompt_version']!r}"
    )
    assert _plan_json_prompt_version(db_path, cid) == "director_planner:v3", (
        "chapters.plan_json 的 prompt_version 必须与 scene_plan 同源，不得落模型回声"
    )


def test_scene_plan_persisted_prompt_version_falls_back_to_constant(tmp_path: Path):
    """未 sync 任何提示词时两个落库面都回落常量——与 payload 侧回退行为逐字一致。"""
    from packages.workflows.chapter_plan.scene_plan_store import load_scene_plan

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)

    _run_save_plan(db_path, pid, cid, "director_planner:v999")

    row = load_scene_plan(db_path, cid)
    assert row is not None, "有 scene_plan 时必须落库"
    assert row["prompt_version"] == "director_planner:v2", (
        f"无 ACTIVE 行时 chapter_scene_plans 必须回落常量 director_planner:v2，"
        f"实得 {row['prompt_version']!r}"
    )
    assert _plan_json_prompt_version(db_path, cid) == "director_planner:v2", (
        "无 ACTIVE 行时 chapters.plan_json 也必须回落常量，不得把回声 v999 落库"
    )
