# -*- coding: utf-8 -*-
"""提示词自愈：DB 镜像落后于仓内源码时自动推进（2026-10-23 用户报「章名不生成」的根因修）。

缺陷形状（AGENTS 坑区新形状：**长驻服务的提示词镜像会陈旧**）
----------------------------------------------------------
``prompts`` 表是 ``docs/agents/prompts/*.md`` 的**运行时镜像**，但它只在**服务启动时**
同步一次（``packages/core/api/main.py`` lifespan）。长驻服务不重启 ⇒ 仓内新增的
提示词版本**永远到不了运行时**。

实测事故（用户原话：「原计划只有 10 章，后面需要新增章节，按软件的『新增章节』后，
再进去『生成计划』，此章节的章节名不会自动生成」）：

* 仓内已有 ``director_planner-v4.md``（§7 输出契约新增顶层 ``title``），落库侧
  章名回填代码（``chapter_plan/pipeline.py``）也已就位；
* 但活库 ``prompts`` 表里 director_planner 只到 **v2**（实测），而 **v2 / v3 的 §7
  都没有**顶层 ``title`` ⇒ 模型**不可能**产出该键 ⇒
  ``director_output.get("title")`` 恒 None ⇒ **回填的 UPDATE 根本不执行**
  ⇒ ``chapters.title`` 永远为空，且**全程零告警**（用户看到的只是一张空标题的章节）。

本文件看守自愈机制的五条性质（断言打在**真实值**上：真实 ACTIVE 版本 / 真实 content）：

1. **忠实镜像 ⇒ 自动推进**到仓内最新版本；
2. **非忠实镜像（人工改过 / 测试库自造）⇒ 一律不动**（不覆盖作者与测试的改写）；
3. **没有 ACTIVE 行 ⇒ 不越权建库**（建库归启动 sync）；
4. **单调**（仓内回滚不降级）+ **幂等**（第二次调用无变化）；
5. **运行期真的用上了新版本**（``ai_call_logs.prompt_version`` 打真实值），
   且 ``NOVELOS_PROMPT_SYNC=off`` 时**不推进**。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.agent_runtime.prompts import PromptRegistry
from packages.core.agent_runtime.runner import create_adhoc_run, run_agent
from packages.core.db import apply_migrations, get_connection

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"
PROMPTS_DIR = REPO_ROOT / "docs" / "agents" / "prompts"
DP_V2 = PROMPTS_DIR / "director_planner-v2.md"
DP_V4 = PROMPTS_DIR / "director_planner-v4.md"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "selfheal.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _docs_dir(tmp_path: Path, name: str = "prompts") -> Path:
    docs = tmp_path / name
    docs.mkdir(parents=True, exist_ok=True)
    return docs


def _active_label(db_path: Path, agent: str) -> str | None:
    try:
        return PromptRegistry(db_path).get_active_prompt(agent)[1]
    except Exception:  # noqa: BLE001 —— 测试辅助：无 ACTIVE 行即 None
        return None


def _active_content(db_path: Path, agent: str) -> str | None:
    try:
        return PromptRegistry(db_path).get_active_prompt(agent)[2]
    except Exception:  # noqa: BLE001
        return None


def _set_content(db_path: Path, agent: str, content: str) -> None:
    """直接改 DB 里该 agent 的 ACTIVE 行内容（模拟人工改写 / 测试自造）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE prompts SET content = ? WHERE agent_id = "
            "(SELECT agent_id FROM agents WHERE name = ?) AND status = 'ACTIVE'",
            (content, agent),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. 忠实镜像 ⇒ 自动推进
# ---------------------------------------------------------------------------


def test_faithful_mirror_is_advanced_to_newest_repo_version(tmp_path: Path):
    """DB 是仓内文件的忠实拷贝 + 仓内新增了版本 ⇒ 自动推进到最新。"""
    db_path = _fresh_db(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "demo-v1.md").write_text("# demo v1\n", encoding="utf-8")

    registry = PromptRegistry(db_path)
    registry.sync_from_docs(docs)
    assert _active_label(db_path, "demo") == "demo:v1"

    # 仓内源码推进（长驻服务没重启 ⇒ DB 还没跟上）
    (docs / "demo-v2.md").write_text("# demo v2\n", encoding="utf-8")

    label = registry.ensure_agent_prompt_current("demo", docs)

    assert label == "demo:v2", "忠实镜像必须被推进到仓内最新版本"
    assert _active_label(db_path, "demo") == "demo:v2"
    assert _active_content(db_path, "demo") == "# demo v2\n", "推进后取到的必须是新版本正文"


def test_all_missing_versions_are_registered_not_only_the_newest(tmp_path: Path):
    """跨越多个版本时**逐个补齐**（v1→v4 ⇒ v2 / v3 / v4 都在库里），不留版本空洞。"""
    db_path = _fresh_db(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "demo-v1.md").write_text("# demo v1\n", encoding="utf-8")
    registry = PromptRegistry(db_path)
    registry.sync_from_docs(docs)

    for n in (2, 3, 4):
        (docs / f"demo-v{n}.md").write_text(f"# demo v{n}\n", encoding="utf-8")

    registry.ensure_agent_prompt_current("demo", docs)

    versions = {p["version"] for p in registry.list_prompts("demo")}
    assert {"v1", "v2", "v3", "v4"} <= versions, f"版本链有空洞：{versions}"


# ---------------------------------------------------------------------------
# 2. 非忠实镜像 ⇒ 一律不动
# ---------------------------------------------------------------------------


def test_customized_prompt_row_is_never_overwritten(tmp_path: Path):
    """当前 ACTIVE 行与仓内同名同版本文件**不一致**（人工改过）⇒ 不推进、不覆盖。"""
    db_path = _fresh_db(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "demo-v1.md").write_text("# demo v1\n", encoding="utf-8")
    registry = PromptRegistry(db_path)
    registry.sync_from_docs(docs)

    _set_content(db_path, "demo", "# 作者自己改过的提示词\n")
    (docs / "demo-v2.md").write_text("# demo v2\n", encoding="utf-8")

    label = registry.ensure_agent_prompt_current("demo", docs)

    assert label is None, "非忠实镜像不得被推进（否则会覆盖人工改写）"
    assert _active_label(db_path, "demo") == "demo:v1"
    assert _active_content(db_path, "demo") == "# 作者自己改过的提示词\n", (
        "人工改写的提示词正文必须原样保留"
    )


def test_absent_prompt_is_not_created(tmp_path: Path):
    """DB 里没有该 agent 的 ACTIVE 行 ⇒ 不越权建库（建库归启动 sync 的职责）。"""
    db_path = _fresh_db(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "demo-v1.md").write_text("# demo v1\n", encoding="utf-8")

    registry = PromptRegistry(db_path)
    label = registry.ensure_agent_prompt_current("demo", docs)

    assert label is None
    assert registry.list_prompts("demo") == [], "自愈不得在无 ACTIVE 行时擅自建库"
    assert all(a["name"] != "demo" for a in registry.list_agents())


def test_already_current_returns_none(tmp_path: Path):
    """仓内没有更新版本 ⇒ 无事可做（返回 None，调用方不必重新取提示词）。"""
    db_path = _fresh_db(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "demo-v1.md").write_text("# demo v1\n", encoding="utf-8")
    registry = PromptRegistry(db_path)
    registry.sync_from_docs(docs)

    assert registry.ensure_agent_prompt_current("demo", docs) is None


# ---------------------------------------------------------------------------
# 3. 单调 + 幂等
# ---------------------------------------------------------------------------


def test_never_downgrades_when_repo_rolled_back(tmp_path: Path):
    """仓内版本被回滚（当前版本文件没了）⇒ 运行时**保持高版本**，绝不降级。"""
    db_path = _fresh_db(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "demo-v1.md").write_text("# demo v1\n", encoding="utf-8")
    (docs / "demo-v2.md").write_text("# demo v2\n", encoding="utf-8")
    registry = PromptRegistry(db_path)
    registry.sync_from_docs(docs)
    assert _active_label(db_path, "demo") == "demo:v2"

    # 仓内回滚：v2 文件被删，只剩 v1
    (docs / "demo-v2.md").unlink()

    assert registry.ensure_agent_prompt_current("demo", docs) is None
    assert _active_label(db_path, "demo") == "demo:v2", "自愈只单调向上，绝不降级"


def test_second_call_is_idempotent(tmp_path: Path):
    """第二次调用无变化 ⇒ 返回 None（不重复写库、不刷 updated_at）。"""
    db_path = _fresh_db(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "demo-v1.md").write_text("# demo v1\n", encoding="utf-8")
    registry = PromptRegistry(db_path)
    registry.sync_from_docs(docs)
    (docs / "demo-v2.md").write_text("# demo v2\n", encoding="utf-8")

    first = registry.ensure_agent_prompt_current("demo", docs)
    second = registry.ensure_agent_prompt_current("demo", docs)

    assert first == "demo:v2"
    assert second is None, "已推进到最新后再次调用必须无变化"


def test_deactivated_newer_version_is_not_forced_active(tmp_path: Path):
    """仓内最新版本在库里但被人工置为非 ACTIVE ⇒ **不强制改回**，如实返回「没推进」。

    守卫：``_upsert_prompt`` 对内容相同的行完全跳过 UPDATE（V3.9 检修：不覆盖人工审计
    信息）。自愈不得借机把人工停用的提示词偷偷启用。
    """
    db_path = _fresh_db(tmp_path)
    docs = _docs_dir(tmp_path)
    (docs / "demo-v1.md").write_text("# demo v1\n", encoding="utf-8")
    registry = PromptRegistry(db_path)
    registry.sync_from_docs(docs)

    # 仓内新增 v2，并先把 v2 行落库后置为 DEPRECATED（模拟「人工停用」）
    (docs / "demo-v2.md").write_text("# demo v2\n", encoding="utf-8")
    registry.sync_from_docs(docs)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE prompts SET status = 'DEPRECATED' WHERE version = 'v2' "
            "AND agent_id = (SELECT agent_id FROM agents WHERE name = 'demo')"
        )
        conn.commit()
    finally:
        conn.close()
    assert _active_label(db_path, "demo") == "demo:v1"

    label = registry.ensure_agent_prompt_current("demo", docs)

    assert label is None, "人工停用的版本不得被自愈偷偷启用"
    assert _active_label(db_path, "demo") == "demo:v1"


# ---------------------------------------------------------------------------
# 4. 运行期真的用上了新版本（端到端）
# ---------------------------------------------------------------------------


def _stage_stale_director_planner(tmp_path: Path) -> tuple[Path, Path]:
    """造一个「活库停在与仓内 v2 同一版、但仓内已有 v3/v4」的库。

    DB 里的 v2 行内容 = 仓内 ``director_planner-v2.md`` **逐字一致** ⇒ 满足忠实镜像守卫。
    """
    db_path = _fresh_db(tmp_path)
    stage = _docs_dir(tmp_path, "stage")
    (stage / "director_planner-v2.md").write_text(
        DP_V2.read_text(encoding="utf-8"), encoding="utf-8"
    )
    PromptRegistry(db_path).sync_from_docs(stage)
    assert _active_label(db_path, "director_planner") == "director_planner:v2"
    return db_path, stage


def test_run_agent_uses_advanced_prompt_version(tmp_path: Path):
    """端到端：run_agent 取提示词时自愈 ⇒ ai_call_logs 记的是**新版本**。"""
    db_path, _stage = _stage_stale_director_planner(tmp_path)
    run_id = create_adhoc_run(db_path, "selfheal-test")

    run_agent(
        db_path,
        "director_planner",
        {"chapter": {"title": None}},
        run_id,
        mock_script=['{"schema_version": "director-plan.v1", "title": "夜叩青石"}'],
        expected=None,
    )

    assert _active_label(db_path, "director_planner") == "director_planner:v4", (
        "运行期必须把提示词推进到仓内最新版本（否则新契约永远到不了运行时）"
    )
    assert _active_content(db_path, "director_planner") == DP_V4.read_text(encoding="utf-8")

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT prompt_version FROM ai_call_logs WHERE run_id = ?", (run_id,)
        ).fetchall()
    finally:
        conn.close()
    assert rows, "本次调用必须落 ai_call_logs"
    assert rows[-1]["prompt_version"] == "director_planner:v4", (
        "调用记录里的提示词版本必须是**实际下发**的那一版（新版本），不是旧版"
    )


def test_run_agent_does_not_advance_when_prompt_sync_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``NOVELOS_PROMPT_SYNC=off`` ⇒ 不推进（与启动 sync 同一个开关，两端一起关）。"""
    db_path, _stage = _stage_stale_director_planner(tmp_path)
    run_id = create_adhoc_run(db_path, "selfheal-off")
    monkeypatch.setenv("NOVELOS_PROMPT_SYNC", "off")

    run_agent(
        db_path,
        "director_planner",
        {"chapter": {"title": None}},
        run_id,
        mock_script=['{"schema_version": "director-plan.v1"}'],
        expected=None,
    )

    assert _active_label(db_path, "director_planner") == "director_planner:v2", (
        "开关关闭时运行时不得自行改动提示词"
    )
