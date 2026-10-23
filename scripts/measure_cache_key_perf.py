#!/usr/bin/env python
"""装配缓存键性能实测（台账登记项「缓存键性能真实规模未测」）。

用途
----
在**临时库 + 合成夹具**下，测出 Context Engine 装配缓存的三个真实数字：

1. **命中路径**耗时分布（p50 / p95 / max）；
2. **未命中路径**耗时分布（p50 / p95 / max）；
3. **键构造** / **查缓存** / **装配**三段的分段耗时 + SQL 语句条数。

口径与边界（**先读再看**）
--------------------------
* **绝不碰活库**：默认在仓库 ``tmp/``（已 gitignore）或系统 ``%TEMP%`` 下建临时库；
  显式传入活库路径（仓库 ``data/`` 下、或 ``NOVELOS_DB_PATH`` / 配置指向的运行库）
  ⇒ **拒绝并以非 0 退出码退出**（:data:`EXIT_LIVE_DB_REFUSED`）。
* **只造合成夹具**：章节号 / 状态 / 标题 / 大纲 / 正文全部是程序生成的假文本，
  **不含任何真实小说内容**，也不从既有库里拷贝。
* **不发 LLM 调用**：只跑纯装配（``build_director_input`` / ``build_writer_input``），
  不跑 write / revise / 审稿，不碰 provider。
* **量具有效性**：缓存是**进程内** dict ⇒ 首次调用含惰性初始化 / 页缓存预热成本。
  ⇒ 统计前**必做 warm-up**，且**逐次校验命中/未命中**（探针记录每次
  ``_cache_get`` 的返回值），命中路径若出现「其实是 miss」直接报错，不出数字。

用法::

    python scripts/measure_cache_key_perf.py                       # 默认 12/24/48 章
    python scripts/measure_cache_key_perf.py --chapters 6 --iterations 5
    python scripts/measure_cache_key_perf.py --db tmp/perf.db      # 指定临时库基路径
    python scripts/measure_cache_key_perf.py --json out.json       # 落原始结果

退出码：0 正常；3 拒绝执行（活库路径 / 位置不合法）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXIT_OK = 0
EXIT_LIVE_DB_REFUSED = 3

#: 合成夹具用的假字符表（**非任何真实作品内容**）。
SYNTH_ALPHABET = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥山水火土日月星辰风云雷电"
SYNTH_TS = "2026-10-23T00:00:00Z"


# ---------------------------------------------------------------------------
# 合成夹具
# ---------------------------------------------------------------------------


def synth_text(seed: int, length: int) -> str:
    """确定性合成文本（纯假内容）。"""
    n = len(SYNTH_ALPHABET)
    start = (seed * 17 + 3) % n
    return "".join(SYNTH_ALPHABET[(start + k) % n] for k in range(length))


def synth_json(seed: int, extra: dict[str, Any] | None = None) -> str:
    payload = {
        "synthetic": True,
        "marker": f"合成夹具-{seed}",
        "items": [f"合成条目-{seed}-{i}" for i in range(4)],
        "note": synth_text(seed, 60),
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def build_synthetic_fixture(
    db_path: Path,
    n_chapters: int,
    *,
    n_characters: int = 12,
    n_locations: int = 8,
    n_factions: int = 5,
    n_world_rules: int = 6,
    n_hooks: int = 10,
    n_debts: int = 6,
    n_plot_events: int = 12,
    n_style_samples: int = 2,
    n_reveal_policies: int = 3,
    body_chars: int = 1200,
) -> dict[str, Any]:
    """建临时库 + 灌**合成**夹具，返回工程标识。

    行数规模刻意与「真实中长篇项目」同形（项目级表：角色/地点/势力/世界规则/
    伏笔/债务/情节事件/文风样例/披露策略），章节表按 ``n_chapters`` 缩放。
    """
    from packages.core.db import apply_migrations, get_connection

    apply_migrations(db_path)

    project_id = f"prj_perf_ch{n_chapters}"
    volume_id = f"vol_perf_{n_chapters}"
    suffix = f"ch{n_chapters}"

    conn = get_connection(db_path)
    try:
        # --- 题材包（projects.genre_pack_id 的目标） ---
        conn.execute(
            "INSERT INTO genre_packs(pack_id, name, genre_tag, version, payload_json,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (
                f"gp_{suffix}", f"合成题材包-{suffix}", "synthetic", 1,
                synth_json(900, {"style_constraints": {"genre_style": {"priority": "medium"}}}),
                SYNTH_TS, SYNTH_TS,
            ),
        )

        conn.execute(
            "INSERT INTO projects(project_id, name, premise, genre, target_words, status,"
            " created_at, updated_at, writing_bible, word_band_json, genre_pack_id,"
            " foreshadow_overdue_chapters) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id, f"合成性能夹具项目-{suffix}", "合成前提，仅用于性能测量。",
                "synthetic", 2200, "ACTIVE", SYNTH_TS, SYNTH_TS,
                "合成写作圣经：节奏要求、禁写清单（假内容）。",
                json.dumps({"low": 1800, "high": 2600}, ensure_ascii=False),
                f"gp_{suffix}", 30,
            ),
        )

        conn.execute(
            "INSERT INTO volumes(volume_id, project_id, number, title, status,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (volume_id, project_id, 1, f"合成卷-{suffix}", "active", SYNTH_TS, SYNTH_TS),
        )

        # --- agents / prompts（让 active_prompt_label 走生产路径而非 fallback） ---
        for agent in ("director", "writer"):
            conn.execute(
                "INSERT INTO agents(agent_id, name, role, config_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (f"ag_{suffix}_{agent}", agent, "reasoning", "{}", SYNTH_TS, SYNTH_TS),
            )
            conn.execute(
                "INSERT INTO prompts(prompt_id, agent_id, version, content, status,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    f"prm_{suffix}_{agent}", f"ag_{suffix}_{agent}", "v1",
                    "合成提示词正文（假内容）。", "ACTIVE", SYNTH_TS, SYNTH_TS,
                ),
            )

        # --- 角色 ---
        for i in range(n_characters):
            cid = f"char_{suffix}_{i:02d}"
            conn.execute(
                "INSERT INTO characters(character_id, project_id, name, role, core_json,"
                " visibility, who_knows, aliases, inject_mode, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cid, project_id, f"合成角色{i:02d}",
                    "protagonist" if i == 0 else "supporting",
                    synth_json(100 + i), "PUBLIC", None,
                    json.dumps([f"别名{i}"]), "auto", SYNTH_TS, SYNTH_TS,
                ),
            )
            conn.execute(
                "INSERT INTO character_states(character_id, state_version, state_json,"
                " visibility, who_knows, created_at) VALUES (?,?,?,?,?,?)",
                (
                    cid, 1, synth_json(200 + i), "VISIBLE", None, SYNTH_TS,
                ),
            )

        # --- 世界观三表 ---
        for i in range(n_locations):
            conn.execute(
                "INSERT INTO locations(location_id, project_id, name, statement, data_json,"
                " visibility, who_knows, aliases, inject_mode, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"loc_{suffix}_{i:02d}", project_id, f"合成地点{i:02d}",
                    f"合成地点说明 {i}。", synth_json(300 + i), "PUBLIC", None,
                    "[]", "auto", SYNTH_TS, SYNTH_TS,
                ),
            )
        for i in range(n_factions):
            conn.execute(
                "INSERT INTO factions(faction_id, project_id, name, statement, data_json,"
                " visibility, who_knows, aliases, inject_mode, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"fac_{suffix}_{i:02d}", project_id, f"合成势力{i:02d}",
                    f"合成势力说明 {i}。", synth_json(400 + i), "VISIBLE", None,
                    "[]", "auto", SYNTH_TS, SYNTH_TS,
                ),
            )
        for i in range(n_world_rules):
            conn.execute(
                "INSERT INTO world_rules(world_rule_id, project_id, name, statement, data_json,"
                " visibility, who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"wr_{suffix}_{i:02d}", project_id, f"合成世界规则{i:02d}",
                    f"合成规则正文 {i}。", synth_json(500 + i), "PUBLIC", None,
                    SYNTH_TS, SYNTH_TS,
                ),
            )

        # --- 章节（章号连续 1..N） ---
        chapter_ids: list[str] = []
        for i in range(1, n_chapters + 1):
            chid = f"ch_{suffix}_{i:03d}"
            chapter_ids.append(chid)
            status = "COMMITTED" if i < n_chapters else "DRAFTED"
            conn.execute(
                "INSERT INTO chapters(chapter_id, project_id, number, title, plan_json,"
                " status, visibility, who_knows, created_at, updated_at, outline_json,"
                " volume_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    chid, project_id, i, f"合成标题{i:03d}",
                    json.dumps(
                        {
                            "chapter_goal": f"合成目标{i}",
                            "core_conflict": f"合成冲突{i}",
                            "turning_point": f"合成转折{i}",
                            "key_beats": [f"合成节拍{i}-a", f"合成节拍{i}-b"],
                            "notes_for_planner": "合成备注",
                        },
                        ensure_ascii=False,
                    ),
                    status, "VISIBLE", None, SYNTH_TS, SYNTH_TS,
                    json.dumps(
                        {
                            "chapter_goal": f"合成大纲目标{i}",
                            "core_conflict": f"合成大纲冲突{i}",
                            "turning_point": f"合成大纲转折{i}",
                            "key_beats": [f"合成大纲节拍{i}-a", f"合成大纲节拍{i}-b"],
                        },
                        ensure_ascii=False,
                    ),
                    volume_id,
                ),
            )

        # --- 摘要 + 草稿正文（合成） ---
        for i, chid in enumerate(chapter_ids, start=1):
            conn.execute(
                "INSERT INTO chapter_summaries(summary_id, project_id, chapter_id, chapter_no,"
                " summary, tail_text, created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    f"sum_{suffix}_{i:03d}", project_id, chid, i,
                    f"合成摘要{i}：" + synth_text(i, 180),
                    f"合成尾段{i}：" + synth_text(i + 700, 280),
                    SYNTH_TS,
                ),
            )
            conn.execute(
                "INSERT INTO drafts(draft_id, chapter_id, version, content, created_by,"
                " prompt_version, model_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"dr_{suffix}_{i:03d}", chid, 1,
                    f"合成正文{i}：" + synth_text(i + 1300, body_chars),
                    "agent:writer:v1", "writer:v1", "synthetic/local", SYNTH_TS,
                ),
            )

        # --- 场景计划（writer 消费） ---
        target_chapter_id = chapter_ids[-1]
        scene_plan = {
            "scenes": [
                {
                    "scene_id": f"sc_{suffix}_{k}",
                    "summary": f"合成场景{k}",
                    "purpose": f"合成场景目的{k}",
                    "characters": [f"合成角色{k % max(1, n_characters):02d}"],
                    "target_words": 500,
                }
                for k in range(1, 4)
            ],
            "purpose": "合成场景计划目的",
            "conflict": "合成场景计划冲突",
            "turn": "合成场景计划转折",
            "characters": ["合成角色00"],
        }
        conn.execute(
            "INSERT INTO chapter_scene_plans(scene_plan_id, chapter_id, project_id, source,"
            " prompt_version, scene_count, payload_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"csp_{suffix}", target_chapter_id, project_id, "director_planner",
                "director_planner:v1", len(scene_plan["scenes"]),
                json.dumps(scene_plan, ensure_ascii=False), SYNTH_TS, SYNTH_TS,
            ),
        )

        # --- 分支 / 提交链（story_states 需要 commit_id 外键） ---
        conn.execute(
            "INSERT INTO branches(branch_id, project_id, name, parent_branch_id,"
            " base_state_version, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (f"br_{suffix}", project_id, "main", None, 0, "ACTIVE", SYNTH_TS),
        )
        conn.execute(
            "INSERT INTO workflows(workflow_id, name, version, definition_json, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?)",
            (f"wf_{suffix}", "chapter-commit", "v1", "{}", SYNTH_TS, SYNTH_TS),
        )
        conn.execute(
            "INSERT INTO workflow_runs(run_id, workflow_id, chapter_id, status, started_at,"
            " ended_at) VALUES (?,?,?,?,?,?)",
            (f"wfr_{suffix}", f"wf_{suffix}", target_chapter_id, "COMPLETED", SYNTH_TS, SYNTH_TS),
        )
        conn.execute(
            "INSERT INTO state_deltas(delta_id, chapter_id, workflow_run_id,"
            " previous_state_version, delta_version, schema_version, payload_json, status,"
            " created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"dlt_{suffix}", target_chapter_id, f"wfr_{suffix}", 0, 1, "state-delta-v0",
                json.dumps({"changes": []}), "applied", "agent:observer:v1", SYNTH_TS,
            ),
        )
        conn.execute(
            "INSERT INTO commits(commit_id, project_id, branch_id, chapter_id,"
            " previous_state_version, resulting_state_version, delta_id, validation_json,"
            " author_approval_json, timestamp, workflow_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"cmt_{suffix}", project_id, f"br_{suffix}", target_chapter_id, 0, 1,
                f"dlt_{suffix}", "{}", "{}", SYNTH_TS, f"wfr_{suffix}",
            ),
        )
        conn.execute(
            "INSERT INTO story_states(project_id, state_version, snapshot_json, commit_id,"
            " created_at) VALUES (?,?,?,?,?)",
            (
                project_id, 1,
                json.dumps({"version": 1, "characters": {}, "threads": {}}, ensure_ascii=False),
                f"cmt_{suffix}", SYNTH_TS,
            ),
        )

        # --- 伏笔 / 叙事债务 / 情节图 ---
        for i in range(n_hooks):
            conn.execute(
                "INSERT INTO hooks(hook_id, project_id, name, introduced_chapter_id, status,"
                " importance, expected_payoff_chapter_id, payoff_chapter_id, visibility,"
                " who_knows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"hook_{suffix}_{i:02d}", project_id, f"合成伏笔{i:02d}",
                    chapter_ids[i % n_chapters],
                    "OPEN" if i % 2 == 0 else "RESOLVED",
                    0.5, chapter_ids[(i + 3) % n_chapters], None, "RESTRICTED", None,
                    SYNTH_TS, SYNTH_TS,
                ),
            )
        for i in range(n_debts):
            conn.execute(
                "INSERT INTO narrative_debts(debt_id, project_id, description, created_chapter_id,"
                " severity, deadline_chapter_id, status, visibility, who_knows, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"debt_{suffix}_{i:02d}", project_id, f"合成债务{i:02d}",
                    chapter_ids[i % n_chapters], 0.5, chapter_ids[(i + 5) % n_chapters],
                    "open", "RESTRICTED", None, SYNTH_TS, SYNTH_TS,
                ),
            )
        for i in range(n_plot_events):
            conn.execute(
                "INSERT INTO plot_events(event_id, project_id, type, cause_json, effects_json,"
                " participants_json, location_id, time_json, status, introduced_chapter_id,"
                " visibility, who_knows) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"evt_{suffix}_{i:02d}", project_id,
                    "conflict" if i % 2 == 0 else "revelation",
                    "[]", "[]",
                    json.dumps([f"char_{suffix}_{i % n_characters:02d}"]),
                    f"loc_{suffix}_{i % n_locations:02d}",
                    json.dumps({"timeline_day": i + 1}),
                    "planned" if i % 3 else "recorded",
                    chapter_ids[i % n_chapters], "RESTRICTED", None,
                ),
            )

        # --- 文风样例 / 披露策略 ---
        for i in range(n_style_samples):
            conn.execute(
                "INSERT INTO author_style_samples(sample_id, project_id, title, content,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (
                    f"asty_{suffix}_{i}", project_id, f"合成样例{i}",
                    synth_text(1500 + i, 900), SYNTH_TS, SYNTH_TS,
                ),
            )
        for i in range(n_reveal_policies):
            conn.execute(
                "INSERT INTO reveal_policies(policy_id, project_id, target_kind, target_id,"
                " reveal_by_chapter, audience, status, notes, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"rp_{suffix}_{i}", project_id, "character",
                    f"char_{suffix}_{i:02d}", 10 + i, "reader", "planned", "合成策略", SYNTH_TS,
                    SYNTH_TS,
                ),
            )

        # --- skill 绑定（键维度 skills_fp 的来源之一） ---
        conn.execute(
            "INSERT INTO skills(skill_id, name, description, version, payload_json, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?)",
            (f"sk_{suffix}", "合成技能", "合成技能说明", 1, "{}", SYNTH_TS, SYNTH_TS),
        )
        conn.execute(
            "INSERT INTO project_skills(project_id, skill_id, ord) VALUES (?,?,?)",
            (project_id, f"sk_{suffix}", 0),
        )

        conn.commit()
    finally:
        conn.close()

    # --- FTS 索引（走生产 upsert 路径：drafts 正文 → chapter_fts） ---
    from packages.core.retrieval.service import upsert_chapter

    for chid in chapter_ids:
        upsert_chapter(str(db_path), chid)

    return {
        "project_id": project_id,
        "volume_id": volume_id,
        "chapter_ids": chapter_ids,
        "target_chapter_id": target_chapter_id,
        "target_chapter_no": n_chapters,
        "scene_plan": scene_plan,
        "n_chapters": n_chapters,
    }


# ---------------------------------------------------------------------------
# 活库守卫
# ---------------------------------------------------------------------------


def _live_db_paths() -> set[Path]:
    """所有「活库」候选路径（只读配置，不建目录、不连库）。"""
    paths: set[Path] = set()
    env_db = os.environ.get("NOVELOS_DB_PATH")
    if env_db:
        paths.add(Path(env_db).expanduser().resolve())
    env_dir = os.environ.get("NOVELOS_DATA_DIR")
    if env_dir:
        paths.add((Path(env_dir).expanduser().resolve() / "novelos.db"))
    try:
        from packages.core.config import get_settings

        paths.add(Path(get_settings().db_path).expanduser().resolve())
    except Exception:  # noqa: BLE001 —— 配置读不到不该让守卫失效
        pass
    paths.add((REPO_ROOT / "data" / "novelos.db").resolve())
    return paths


def _forbidden_roots() -> list[Path]:
    """内容仓 / 痕迹仓 + 仓库 ``data/``（三者一律禁写）。"""
    roots = [
        REPO_ROOT / "data",
        REPO_ROOT.parent / "NovelOS-Content",
        REPO_ROOT.parent / "NovelOS-Artifacts",
    ]
    return [r.resolve() for r in roots if r.exists()]


def assert_not_live_db(db_path: Path) -> None:
    """拒绝把测量指向活库 / 内容仓 / 仓库 ``data/``。违反 ⇒ 抛 :class:`SystemExit`。"""
    resolved = db_path.expanduser().resolve()
    live = _live_db_paths()
    if resolved in live:
        print(
            f"[拒绝] {resolved} 是活库（运行库）路径——本脚本只允许临时库。", file=sys.stderr,
        )
        raise SystemExit(EXIT_LIVE_DB_REFUSED)
    for root in _forbidden_roots():
        if resolved == root or root in resolved.parents:
            print(
                f"[拒绝] {resolved} 位于禁写目录 {root} 内"
                f"（内容仓 / 痕迹仓 / 仓库 data/）——本脚本只允许临时库。", file=sys.stderr,
            )
            raise SystemExit(EXIT_LIVE_DB_REFUSED)


# ---------------------------------------------------------------------------
# 打点探针（分段计时 + 命中校验 + SQL 计数）
# ---------------------------------------------------------------------------


class AssemblyProbe:
    """给装配入口打点。

    分段口径（一次调用的时间轴）::

        call_start ──[ 键构造 ]── get_enter ──[ 查缓存 ]── get_exit ──[ 装配 ]── call_end
                              │                             │                   │
                    peek + 13 个指纹 + 键元组        dict.get + deepcopy    全量装配 + 写缓存

    「键构造」= ``call_start → get_enter``（含装饰器建连、peek、全部指纹计算）。
    「查缓存」= ``get_enter → get_exit``（``_cache_get`` 内部：dict 查找 + 深拷贝）。
    「装配」  = ``get_exit → call_end``（未命中时的全量装配 + ``_cache_put`` 深拷贝）。

    SQL 计数**分两档**（这是本脚本的关键口径修正）：
    - ``sql_app``：应用层语句（业务 SQL + PRAGMA 建连设置）；
    - ``sql_fts``：**FTS5 内部语句**（SQLite 给 FTS5 虚表的内部查询，语句文本以
      ``--`` 开头——FTS5 用这个前缀标记内部语句）。

    ⚠️ **不分开就会得出反向结论**：FTS5 内部语句条数取决于「查询词命中了多少文档」
    （可多可少，**不随章数单调**），把它算进「SQL 条数」会让人误以为规模越大越快。
    实测：12 章 837 条 / 48 章 306 条，但**应用层语句两者都是 66 条**。
    """

    def __init__(self, module: Any) -> None:
        self.module = module
        self._orig_get = module._cache_get
        self._orig_put = module._cache_put
        self._orig_connect = sqlite3.connect
        self.records: list[dict[str, Any]] = []
        self.sql_app = 0
        self.sql_fts = 0
        self._sql_on = False
        self._call_start_ns = 0

    # --- SQL 计数 ---
    def _trace(self, sql: str) -> None:
        if not self._sql_on:
            return
        if sql.lstrip().startswith("--"):
            self.sql_fts += 1
        else:
            self.sql_app += 1

    def _counting_connect(self, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = self._orig_connect(*args, **kwargs)
        conn.set_trace_callback(self._trace)
        return conn

    # --- 缓存钩子 ---
    def _cache_get(self, key: tuple) -> dict[str, Any] | None:
        t_enter = time.perf_counter_ns()
        value = self._orig_get(key)
        t_exit = time.perf_counter_ns()
        self.records.append(
            {
                "get_enter_ns": t_enter,
                "get_exit_ns": t_exit,
                "hit": value is not None,
                "call_start_ns": self._call_start_ns,
            }
        )
        return value

    def _cache_put(self, key: tuple, value: dict[str, Any]) -> None:
        t_enter = time.perf_counter_ns()
        self._orig_put(key, value)
        t_exit = time.perf_counter_ns()
        if self.records:
            self.records[-1]["put_enter_ns"] = t_enter
            self.records[-1]["put_exit_ns"] = t_exit

    # --- 上下文管理 ---
    def __enter__(self) -> "AssemblyProbe":
        self.module._cache_get = self._cache_get
        self.module._cache_put = self._cache_put
        sqlite3.connect = self._counting_connect  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        self.module._cache_get = self._orig_get
        self.module._cache_put = self._orig_put
        sqlite3.connect = self._orig_connect  # type: ignore[assignment]

    def call(self, fn: Any, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """跑一次装配，返回 ``(payload, 分段耗时 dict)``。"""
        self.records = []
        self.sql_app = 0
        self.sql_fts = 0
        self._sql_on = True
        self._call_start_ns = time.perf_counter_ns()
        try:
            payload = fn(*args, **kwargs)
        finally:
            self._sql_on = False
        call_end_ns = time.perf_counter_ns()
        rec = self.records[-1] if self.records else None
        if rec is None:
            seg = {
                "total_ms": (call_end_ns - self._call_start_ns) / 1e6,
                "key_ms": None, "lookup_ms": None, "assemble_ms": None, "put_ms": None,
                "hit": None, "sql_app": self.sql_app, "sql_fts": self.sql_fts,
            }
            return payload, seg
        key_ms = (rec["get_enter_ns"] - rec["call_start_ns"]) / 1e6
        lookup_ms = (rec["get_exit_ns"] - rec["get_enter_ns"]) / 1e6
        rest_ms = (call_end_ns - rec["get_exit_ns"]) / 1e6
        put_ms = None
        if "put_enter_ns" in rec:
            put_ms = (rec["put_exit_ns"] - rec["put_enter_ns"]) / 1e6
        assemble_ms = rest_ms - (put_ms or 0.0)
        seg = {
            "total_ms": (call_end_ns - self._call_start_ns) / 1e6,
            "key_ms": key_ms,
            "lookup_ms": lookup_ms,
            "assemble_ms": assemble_ms,
            "put_ms": put_ms,
            "hit": rec["hit"],
            "sql_app": self.sql_app,
            "sql_fts": self.sql_fts,
        }
        return payload, seg


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def pct(sorted_vals: list[float], q: float) -> float | None:
    """最近秩（nearest-rank）分位数；空样本返回 None。"""
    if not sorted_vals:
        return None
    k = max(1, math.ceil(q * len(sorted_vals)))
    return sorted_vals[k - 1]


def summarize(samples: list[float]) -> dict[str, Any]:
    s = sorted(samples)
    return {
        "n": len(s),
        "min_ms": s[0] if s else None,
        "p50_ms": pct(s, 0.50),
        "p95_ms": pct(s, 0.95),
        "max_ms": s[-1] if s else None,
        "mean_ms": statistics.fmean(s) if s else None,
    }


# ---------------------------------------------------------------------------
# 测量
# ---------------------------------------------------------------------------


def _set_chapter_title(db_path: Path, chapter_id: str, title: str) -> None:
    """改一个**进缓存键**的 DB 侧维度（``chapters.title`` ⇒ ``title_fp``）。"""
    from packages.core.db import get_connection

    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET title = ?, updated_at = ? WHERE chapter_id = ?",
            (title, SYNTH_TS, chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


def measure_assembly(
    cases: list[tuple[Path, dict[str, Any]]],
    assembly: str,
    *,
    iterations: int,
    warmup: int,
) -> list[dict[str, Any]]:
    """测一个装配器（director / writer）在**多个规模**上的命中 / 未命中路径。

    **跨规模轮转（round-robin）**是这里的关键实验设计：若按「测完 12 章再测 24 章」
    的顺序跑，机器负载漂移会被误读成规模效应。轮转后每个规模的样本均匀分布在
    整段测量窗口里 ⇒ 规模差异与时间漂移解耦。

    三个测量阶段（**分阶段而非混合**）：
    1. ``hit``：同一份输入连取（不写库 ⇒ 键不变 ⇒ 必命中）；
    2. ``miss_db``：每轮把 ``chapters.title`` 改成**新值**（写库在计时窗口外）
       ⇒ ``title_fp`` 变 ⇒ 必未命中；
    3. ``miss_param``：每轮改 ``target_word_count``（运行期参数维度）⇒ 必未命中。

    每一次调用都用探针**逐次校验**命中/未命中；出现反例 ⇒ 直接退出码非 0，不出数字。
    """
    from packages.core.context_engine import director_input, writer_input
    from packages.core.context_engine.cache import _cache_reset

    module = director_input if assembly == "director" else writer_input
    build = (
        director_input.build_director_input
        if assembly == "director"
        else writer_input.build_writer_input
    )
    intent = "合成作者意图：本章节奏收紧（假内容）。"

    def _call(db_path: Path, fx: dict[str, Any], target_words: int) -> dict[str, Any]:
        if assembly == "director":
            return build(
                str(db_path), fx["project_id"], fx["target_chapter_id"], intent,
                target_word_count=target_words,
            )
        return build(
            str(db_path), fx["target_chapter_id"], fx["scene_plan"],
            target_word_count=target_words, author_intent=intent,
        )

    buckets: list[dict[str, list[float]]] = [
        {
            "hit_total": [], "hit_key": [], "hit_lookup": [], "hit_sql_app": [], "hit_sql_fts": [],
            "miss_total": [], "miss_key": [], "miss_lookup": [], "miss_assemble": [],
            "miss_sql_app": [], "miss_sql_fts": [],
            "miss_param_total": [],
            "hit_flags": [], "miss_flags": [], "miss_param_flags": [],
        }
        for _ in cases
    ]
    payload_bytes: list[int | None] = [None] * len(cases)

    with AssemblyProbe(module) as probe:
        # ---- warm-up（不进统计）：惰性初始化 / SQLite 页缓存预热 ----
        warm_first: list[float] = []
        warm_last: list[float] = []
        for db_path, fx in cases:
            for i in range(warmup):
                _payload, seg = probe.call(_call, db_path, fx, 2200)
                if i == 0:
                    warm_first.append(seg["total_ms"])
                warm_last.append(seg["total_ms"])

        # ---- prime（不进统计）：把条目装进缓存；否则第一次调用必然是 miss，
        #      会被误记成「命中路径的一次 miss」⇒ 命中统计失真。 ----
        _cache_reset()
        for db_path, fx in cases:
            _payload, seg = probe.call(_call, db_path, fx, 2200)
            if seg["hit"]:
                raise SystemExit(
                    f"[测量无效] {assembly} prime 调用竟命中——缓存状态与预期不符，不产出数字。"
                )

        # ---- 阶段 1：命中路径（轮转各规模）----
        for _rep in range(iterations):
            for idx, (db_path, fx) in enumerate(cases):
                payload, seg = probe.call(_call, db_path, fx, 2200)
                b = buckets[idx]
                b["hit_total"].append(seg["total_ms"])
                b["hit_key"].append(seg["key_ms"])
                b["hit_lookup"].append(seg["lookup_ms"])
                b["hit_sql_app"].append(float(seg["sql_app"]))
                b["hit_sql_fts"].append(float(seg["sql_fts"]))
                b["hit_flags"].append(bool(seg["hit"]))
                if payload_bytes[idx] is None:
                    payload_bytes[idx] = len(
                        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                    )

        # ---- 阶段 2：未命中路径（DB 侧维度改；写库在计时窗口外）----
        for rep in range(iterations):
            for idx, (db_path, fx) in enumerate(cases):
                _set_chapter_title(
                    db_path, fx["target_chapter_id"], f"合成标题-{assembly}-{rep:04d}",
                )
                _payload, seg = probe.call(_call, db_path, fx, 2200)
                b = buckets[idx]
                b["miss_total"].append(seg["total_ms"])
                b["miss_key"].append(seg["key_ms"])
                b["miss_lookup"].append(seg["lookup_ms"])
                b["miss_assemble"].append(seg["assemble_ms"])
                b["miss_sql_app"].append(float(seg["sql_app"]))
                b["miss_sql_fts"].append(float(seg["sql_fts"]))
                b["miss_flags"].append(bool(seg["hit"]))

        # ---- 阶段 3：未命中路径（运行期参数改）→ 与阶段 2 交叉验证 ----
        for rep in range(iterations):
            for idx, (db_path, fx) in enumerate(cases):
                _payload, seg = probe.call(_call, db_path, fx, 2100 + rep)
                b = buckets[idx]
                b["miss_param_total"].append(seg["total_ms"])
                b["miss_param_flags"].append(bool(seg["hit"]))

    out: list[dict[str, Any]] = []
    for idx, (db_path, fx) in enumerate(cases):
        b = buckets[idx]
        if not all(b["hit_flags"]):
            raise SystemExit(
                f"[测量无效] {assembly} @ {fx['n_chapters']} 章 命中路径出现 miss"
                f"（{b['hit_flags'].count(False)}/{len(b['hit_flags'])} 次）——不产出数字。"
            )
        if any(b["miss_flags"]):
            raise SystemExit(
                f"[测量无效] {assembly} @ {fx['n_chapters']} 章 未命中路径出现 hit"
                f"（{sum(1 for f in b['miss_flags'] if f)}/{len(b['miss_flags'])} 次）——不产出数字。"
            )
        if any(b["miss_param_flags"]):
            raise SystemExit(
                f"[测量无效] {assembly} @ {fx['n_chapters']} 章 参数变更路径出现 hit——不产出数字。"
            )
        out.append(
            {
                "assembly": assembly,
                "chapters": fx["n_chapters"],
                "payload_bytes": payload_bytes[idx],
                "warmup": {
                    "n": warmup,
                    "first_total_ms": warm_first[idx] if warm_first else None,
                    "last_total_ms": warm_last[idx] if warm_last else None,
                },
                "hit": {
                    "total": summarize(b["hit_total"]),
                    "key": summarize(b["hit_key"]),
                    "lookup": summarize(b["hit_lookup"]),
                    "sql_app": summarize(b["hit_sql_app"]),
                    "sql_fts": summarize(b["hit_sql_fts"]),
                    "verified_hits": sum(1 for f in b["hit_flags"] if f),
                },
                "miss_db_dim": {
                    "total": summarize(b["miss_total"]),
                    "key": summarize(b["miss_key"]),
                    "lookup": summarize(b["miss_lookup"]),
                    "assemble": summarize(b["miss_assemble"]),
                    "sql_app": summarize(b["miss_sql_app"]),
                    "sql_fts": summarize(b["miss_sql_fts"]),
                    "verified_misses": sum(1 for f in b["miss_flags"] if not f),
                },
                "miss_param_dim": {
                    "total": summarize(b["miss_param_total"]),
                    "verified_misses": sum(1 for f in b["miss_param_flags"] if not f),
                },
            }
        )
    return out


def measure_key_dimensions(db_path: Path, fx: dict[str, Any]) -> dict[str, Any]:
    """把「键构造」拆到单个指纹维度上（找出最贵的那一维）。"""
    from packages.core.context_engine import cache as cache_mod
    from packages.core.context_engine.builders_common.common import _peek_chapter_context
    from packages.core.db import get_connection

    project_id = fx["project_id"]
    chapter_id = fx["target_chapter_id"]

    dims: dict[str, Any] = {}
    conn = get_connection(db_path)
    try:
        def _time(name: str, fn: Any) -> None:
            # warm-up 一次再统计（缓存模块内部的惰性初始化不计入）
            fn()
            samples = []
            for _ in range(20):
                t0 = time.perf_counter_ns()
                fn()
                samples.append((time.perf_counter_ns() - t0) / 1e6)
            dims[name] = summarize(samples)

        _time("peek", lambda: _peek_chapter_context(db_path, chapter_id, project_id=project_id, conn=conn))
        _time(
            "chars_fp",
            lambda: cache_mod.characters_fingerprint(db_path, project_id, conn=conn),
        )
        _time("world_fp", lambda: cache_mod.world_fingerprint(db_path, project_id, conn=conn))
        _time(
            "reveal_fp",
            lambda: cache_mod.reveal_policies_fingerprint(db_path, project_id, conn=conn),
        )
        _time(
            "style_fp",
            lambda: cache_mod.author_style_samples_fingerprint(db_path, project_id, conn=conn),
        )
        _time(
            "project_meta_fp",
            lambda: cache_mod.project_meta_fingerprint(db_path, project_id, conn=conn),
        )
        _time("hooks_fp", lambda: cache_mod.hooks_fingerprint(db_path, project_id, conn=conn))
        _time("debts_fp", lambda: cache_mod.debts_fingerprint(db_path, project_id, conn=conn))
        _time("plot_fp", lambda: cache_mod.plot_fingerprint(db_path, project_id, conn=conn))
        _time(
            "foreshadow_fp",
            lambda: cache_mod.foreshadow_threshold_fingerprint(db_path, project_id, conn=conn),
        )
        _time(
            "title_fp",
            lambda: cache_mod.chapter_title_fingerprint(db_path, chapter_id, conn=conn),
        )
    finally:
        conn.close()

    # --- 连接级成本（不在指纹里，但每次调用都要付）---
    from packages.core.agent_runtime.prompts import active_prompt_label

    def _conn_open() -> None:
        c = get_connection(db_path)
        c.close()

    _conn_open()
    samples = []
    for _ in range(20):
        t0 = time.perf_counter_ns()
        _conn_open()
        samples.append((time.perf_counter_ns() - t0) / 1e6)
    dims["conn_open_close"] = summarize(samples)

    _time(
        "prompt_label_lookup",
        lambda: active_prompt_label(str(db_path), "director", fallback="director:v1"),
    )
    return dims


def _assemble_call(assembly: str) -> Any:
    """返回 ``call(db_path, fx, target_words) -> payload``（director / writer 签名统一收口）。"""
    from packages.core.context_engine import director_input, writer_input

    intent = "合成作者意图：本章节奏收紧（假内容）。"
    if assembly == "director":
        def _call(db_path: Path, fx: dict[str, Any], target_words: int) -> dict[str, Any]:
            return director_input.build_director_input(
                str(db_path), fx["project_id"], fx["target_chapter_id"], intent,
                target_word_count=target_words,
            )
    else:
        def _call(db_path: Path, fx: dict[str, Any], target_words: int) -> dict[str, Any]:
            return writer_input.build_writer_input(
                str(db_path), fx["target_chapter_id"], fx["scene_plan"],
                target_word_count=target_words, author_intent=intent,
            )
    return _call


def measure_ablation(
    cases: list[tuple[Path, dict[str, Any]]],
    assembly: str,
    *,
    iterations: int,
) -> list[dict[str, Any]]:
    """消融：把「提示词标签查询」stub 成常量返回，量出它在命中路径里的**真实在途成本**。

    ⚠️ stub 必须返回**与真值逐字相同**的标签 ⇒ 缓存键不变 ⇒ 命中路径仍是命中
    （否则键一变就变成未命中，测的就不是同一个东西了）。
    """
    from packages.core.context_engine import director_input, writer_input
    from packages.core.context_engine.cache import _cache_reset

    module = director_input if assembly == "director" else writer_input
    orig_label_fn = module.active_prompt_label
    agent = "director" if assembly == "director" else "writer"
    real_label = orig_label_fn(str(cases[0][0]), agent, fallback=f"{agent}:v1")
    call = _assemble_call(assembly)

    module.active_prompt_label = lambda *a, **k: real_label  # type: ignore[assignment]
    try:
        out: list[dict[str, Any]] = []
        with AssemblyProbe(module) as probe:
            for db_path, fx in cases:
                for _ in range(3):
                    probe.call(call, db_path, fx, 2200)
            _cache_reset()
            for db_path, fx in cases:
                probe.call(call, db_path, fx, 2200)  # prime
            for db_path, fx in cases:
                samples: list[float] = []
                flags: list[bool] = []
                for _ in range(iterations):
                    _payload, seg = probe.call(call, db_path, fx, 2200)
                    samples.append(seg["total_ms"])
                    flags.append(bool(seg["hit"]))
                if not all(flags):
                    raise SystemExit(
                        f"[测量无效] {assembly} 消融组命中路径出现 miss——不产出数字。"
                    )
                out.append({
                    "assembly": assembly,
                    "chapters": fx["n_chapters"],
                    "stubbed_label": real_label,
                    "hit_total": summarize(samples),
                })
        return out
    finally:
        module.active_prompt_label = orig_label_fn  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="measure_cache_key_perf",
        description="装配缓存键性能实测（临时库 + 合成夹具；拒绝活库）",
    )
    p.add_argument(
        "--db",
        default=None,
        help=(
            "临时库**基路径**（每个规模派生 <stem>_ch<N><suffix>）。"
            "必须是临时位置；指向活库 / 仓库 data/ / 内容仓 ⇒ 拒绝并退出码 3。"
            "默认：仓库 tmp/ 下自动命名。"
        ),
    )
    p.add_argument(
        "--chapters",
        default="12,24,48",
        help="规模点（章数，逗号分隔）；默认 12,24,48",
    )
    p.add_argument("--iterations", type=int, default=25, help="每条路径的统计迭代数（默认 25）")
    p.add_argument("--warmup", type=int, default=5, help="统计前的预热次数（默认 5）")
    p.add_argument(
        "--assembly", default="both", choices=["director", "writer", "both"],
        help="测哪个装配器（默认 both）",
    )
    p.add_argument(
        "--entities",
        default="",
        help=(
            "补充规模轴：项目级实体数（角色数，逗号分隔）。"
            "章数固定为 --entity-chapters（默认 48），其余项目级表按角色数同比例放大。"
            "用于验证「命中路径对章数不敏感、对项目级行数敏感」。"
        ),
    )
    p.add_argument("--entity-chapters", type=int, default=48, help="实体规模轴的章数（默认 48）")
    p.add_argument(
        "--entity-only", action="store_true",
        help="只测实体规模轴（跳过章数规模轴）——用于让实体轴在**干净进程**里第一个跑",
    )
    p.add_argument("--json", default=None, help="把原始结果写到该 JSON 文件")
    p.add_argument("--keep-db", action="store_true", help="保留临时库（默认测完删除）")
    p.add_argument(
        "--no-ablation", action="store_true",
        help="跳过消融实验（stub 提示词标签查询，量其在途成本）",
    )
    return p


def _default_base_db() -> Path:
    tmp_dir = REPO_ROOT / "tmp"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir / f"cache_key_perf_{os.getpid()}_{int(time.time())}.db"
    except OSError:
        import tempfile

        return Path(tempfile.gettempdir()) / f"cache_key_perf_{os.getpid()}_{int(time.time())}.db"


#: 子进程标记（both 模式下由父进程设置，避免无限递归）。
_CHILD_ENV = "NOVELOS_PERF_CHILD"


def _child_argv(args: argparse.Namespace, assembly: str, base: Path) -> list[str]:
    """构造子进程 argv（与父进程同口径，只换装配器与临时库基名）。"""
    argv = [
        "--assembly", assembly,
        "--chapters", str(args.chapters),
        "--iterations", str(args.iterations),
        "--warmup", str(args.warmup),
        "--db", str(base.parent / f"{base.stem}_{assembly}{base.suffix or '.db'}"),
    ]
    if args.entities:
        argv += ["--entities", str(args.entities), "--entity-chapters", str(args.entity_chapters)]
    if args.no_ablation:
        argv.append("--no-ablation")
    if args.keep_db:
        argv.append("--keep-db")
    return argv


def _main_both(
    args: argparse.Namespace, scales: list[int], base: Path,
) -> int:
    """both 模式：每个装配器跑**独立子进程**，父进程合并结果。"""
    import subprocess

    merged: list[dict[str, Any]] = []
    entity_merged: list[dict[str, Any]] = []
    env = dict(os.environ)
    env[_CHILD_ENV] = "1"
    for assembly in ("director", "writer"):
        print(f"[子进程] 测 {assembly} …", file=sys.stderr)
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *_child_argv(args, assembly, base)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr or "")
            print(f"[错误] 子进程 {assembly} 退出码 {proc.returncode}", file=sys.stderr)
            return proc.returncode or 1
        data = json.loads(proc.stdout)
        by_chapters = {r["chapters"]: r for r in data["results"]}
        for n in scales:
            entry = merged_by_chapter(merged, n)
            src = by_chapters[n]
            entry.setdefault("chapters", n)
            entry.setdefault("db_path", src.get("db_path"))
            entry.setdefault("project_id", src.get("project_id"))
            entry[assembly] = src[assembly]
        entity_merged.extend(data.get("entity_scale_results") or [])

    payload = {
        "env": {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "platform": sys.platform,
            "clock": "time.perf_counter_ns (monotonic, ns)",
            "percentile": "nearest-rank",
            "iterations": args.iterations,
            "warmup": args.warmup,
            "scales": scales,
            "assembly": args.assembly,
            "process_isolation": "each assembly measured in its own subprocess",
        },
        "results": merged,
        "entity_scale_results": entity_merged,
    }
    _emit(payload, args)
    return EXIT_OK


def merged_by_chapter(merged: list[dict[str, Any]], n: int) -> dict[str, Any]:
    for item in merged:
        if item.get("chapters") == n:
            return item
    item = {"chapters": n}
    merged.append(item)
    return item


def _emit(payload: dict[str, Any], args: argparse.Namespace) -> None:
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[输出] {out}", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK ⇒ 中文输出会乱码/抛 UnicodeEncodeError。
    # 统一按 UTF-8 输出（stdout 的 JSON 结果是纯 ASCII，但 stderr 有中文）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 —— 非 TextIO（如被 pytest 替换）时跳过
            pass

    args = build_parser().parse_args(argv)

    try:
        scales = [int(x) for x in str(args.chapters).split(",") if x.strip()]
    except ValueError:
        print("[错误] --chapters 必须是逗号分隔的整数", file=sys.stderr)
        return 2
    if not scales or any(n <= 0 for n in scales):
        print("[错误] --chapters 必须为正整数", file=sys.stderr)
        return 2
    if args.iterations < 1 or args.warmup < 0:
        print("[错误] --iterations ≥ 1 且 --warmup ≥ 0", file=sys.stderr)
        return 2

    base = Path(args.db) if args.db else _default_base_db()
    assert_not_live_db(base)  # SystemExit ⇒ 退出码 3
    # ⚠️ **父目录不存在 ⇒ 明确报错**（对抗审查 F-6，2026-10-23）：
    # 早先直接往下建库 ⇒ 抛未捕获的 ``sqlite3.OperationalError: unable to open
    # database file`` 栈 ⇒ 用户看不懂（这与「跑挂了」不是一回事）。
    if not base.parent.exists():
        print(
            f"[错误] --db 的父目录不存在：{base.parent}\n"
            "       请先创建该目录，或换一个已存在的路径（本脚本不会替你建目录，"
            "免得打错路径时留下空目录）。",
            file=sys.stderr,
        )
        return 2

    # ---- 「每个装配器一个进程」：同进程连测两个装配器会互相污染（实测命中 p50
    #      从 ~3.1ms 抬到 ~3.7~4.1ms——进程级状态：分配器/GC/模块缓存），
    #      规模对比会被这个漂移带偏 ⇒ both 模式改为**子进程各测各的**，父进程合并。----
    if args.assembly == "both" and not os.environ.get(_CHILD_ENV):
        return _main_both(args, scales, base)

    if args.entity_only:
        # 只跑实体轴：跳过章数轴 ⇒ 实体轴在**干净进程**里第一个被测（不被同进程
        # 的章数轴污染）。夹具只建实体轴需要的那些库。
        entity_results = []
        entity_cases = []
        ent_list = [int(x) for x in str(args.entities).split(",") if x.strip()]
        for n_ent in ent_list:
            db_path = base.parent / f"{base.stem}_ent{n_ent}{base.suffix or '.db'}"
            assert_not_live_db(db_path)
            for stale in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
                if stale.exists():
                    stale.unlink()
            print(f"[夹具] 建实体规模临时库 {db_path}（{n_ent} 角色 / {args.entity_chapters} 章）…",
                  file=sys.stderr)
            fx = build_synthetic_fixture(
                db_path, args.entity_chapters,
                n_characters=n_ent,
                n_locations=max(2, n_ent // 2),
                n_factions=max(2, n_ent // 3),
                n_world_rules=max(2, n_ent // 2),
                n_hooks=max(2, n_ent),
                n_debts=max(2, n_ent // 2),
                n_plot_events=max(2, n_ent),
            )
            fx["n_entities"] = n_ent
            entity_cases.append((db_path, fx))
        for assembly in (["director", "writer"] if args.assembly == "both" else [args.assembly]):
            measured = measure_assembly(
                entity_cases, assembly, iterations=args.iterations, warmup=args.warmup,
            )
            for i, m in enumerate(measured):
                entry = dict(m)
                entry["entities"] = entity_cases[i][1]["n_entities"]
                entity_results.append(entry)
        if not args.keep_db:
            for db_path, _fx in entity_cases:
                for stale in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
                    if stale.exists():
                        stale.unlink()
        payload = {
            "env": {
                "python": sys.version.split()[0],
                "sqlite": sqlite3.sqlite_version,
                "platform": sys.platform,
                "clock": "time.perf_counter_ns (monotonic, ns)",
                "percentile": "nearest-rank",
                "iterations": args.iterations,
                "warmup": args.warmup,
                "scales": [],
                "assembly": args.assembly,
                "process_isolation": "entity axis only, measured first in a fresh process",
            },
            "results": [],
            "entity_scale_results": entity_results,
        }
        _emit(payload, args)
        return EXIT_OK

    # ---- 先把各规模的临时库与合成夹具全部建好（测量阶段不再建库）----
    cases: list[tuple[Path, dict[str, Any]]] = []
    for n in scales:
        db_path = base.parent / f"{base.stem}_ch{n}{base.suffix or '.db'}"
        assert_not_live_db(db_path)
        for stale in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            if stale.exists():
                stale.unlink()
        print(f"[夹具] 建临时库 {db_path}（{n} 章合成数据）…", file=sys.stderr)
        cases.append((db_path, build_synthetic_fixture(db_path, n)))

    per_scale: dict[int, dict[str, Any]] = {
        n: {"chapters": n, "db_path": str(cases[i][0]), "project_id": cases[i][1]["project_id"]}
        for i, n in enumerate(scales)
    }

    # ---- 测量：每个装配器**跨规模轮转**跑 ----
    for assembly in (["director", "writer"] if args.assembly == "both" else [args.assembly]):
        measured = measure_assembly(
            cases, assembly, iterations=args.iterations, warmup=args.warmup,
        )
        for m in measured:
            per_scale[m["chapters"]][assembly] = m
        # 消融：stub 掉「提示词标签查询」⇒ 差值 = 它在命中路径里的在途成本
        if not args.no_ablation:
            for m in measure_ablation(
                cases, assembly, iterations=max(5, args.iterations // 2),
            ):
                per_scale[m["chapters"]][assembly]["ablation_no_prompt_lookup"] = m

    # ---- 键维度分解（把「键构造」拆到单个指纹上）----
    for i, (db_path, fx) in enumerate(cases):
        dims = measure_key_dimensions(db_path, fx)
        for assembly in per_scale[scales[i]]:
            if assembly in ("director", "writer"):
                per_scale[scales[i]][assembly]["key_dimensions"] = dims

    results = [per_scale[n] for n in scales]

    # ---- 补充规模轴：项目级实体数（章数固定）----
    entity_results: list[dict[str, Any]] = []
    entity_cases: list[tuple[Path, dict[str, Any]]] = []
    if args.entities:
        ent_list = [int(x) for x in str(args.entities).split(",") if x.strip()]
        for n_ent in ent_list:
            db_path = base.parent / f"{base.stem}_ent{n_ent}{base.suffix or '.db'}"
            assert_not_live_db(db_path)
            for stale in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
                if stale.exists():
                    stale.unlink()
            print(f"[夹具] 建实体规模临时库 {db_path}（{n_ent} 角色 / {args.entity_chapters} 章）…",
                  file=sys.stderr)
            fx = build_synthetic_fixture(
                db_path, args.entity_chapters,
                n_characters=n_ent,
                n_locations=max(2, n_ent // 2),
                n_factions=max(2, n_ent // 3),
                n_world_rules=max(2, n_ent // 2),
                n_hooks=max(2, n_ent),
                n_debts=max(2, n_ent // 2),
                n_plot_events=max(2, n_ent),
            )
            fx["n_entities"] = n_ent
            entity_cases.append((db_path, fx))
        for assembly in (["director", "writer"] if args.assembly == "both" else [args.assembly]):
            measured = measure_assembly(
                entity_cases, assembly, iterations=args.iterations, warmup=args.warmup,
            )
            for i, m in enumerate(measured):
                entry = dict(m)
                entry["entities"] = entity_cases[i][1]["n_entities"]
                entity_results.append(entry)
        if not args.keep_db:
            for db_path, _fx in entity_cases:
                for stale in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
                    if stale.exists():
                        stale.unlink()

    payload = {
        "env": {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "platform": sys.platform,
            "clock": "time.perf_counter_ns (monotonic, ns)",
            "percentile": "nearest-rank",
            "iterations": args.iterations,
            "warmup": args.warmup,
            "scales": scales,
            "assembly": args.assembly,
            "process_isolation": "each assembly measured in its own subprocess" if args.assembly == "both"
            else "single assembly in this process",
        },
        "results": results,
        "entity_scale_results": entity_results,
    }
    _emit(payload, args)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
