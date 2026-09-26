"""项目写作圣经（``projects.writing_bible``，迁移 0029 + context_engine 装配）。

覆盖：

1. 领域服务往返：create / update / get / list 三态（写入、清除、保留）；
2. 归一：空白串与显式 null 同义（落 NULL），非空文本去首尾空白；
3. 装配注入：圣经进 writer（full + paged 两条路径）与 director payload；
4. 优先级：圣经基线 + 运行期增量**拼接**，增量段声明冲突裁决方向；
   只有一方非空 → 逐字进 payload（无包装）；
5. **缓存键纪律**（AGENTS.md 硬规则 2）：改圣经 → 同一 chapter / state_version 下
   ``build_writer_input`` / ``build_director_input`` 必须**重新装配**并返回新 payload
   （用 spy 统计 uncached 调用次数判定「真 miss」，而非比对文本）；
6. 回归：无圣经项目行为与改造前逐字一致（无运行期意图 → payload 不出现
   ``author_intent`` 键；有运行期意图 → ``raw`` 逐字等于该意图）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.context_engine.builders import (
    _cache_reset,
    build_director_input,
    build_writer_input,
    resolve_author_intent,
)
from packages.core.context_engine.builders_common import _peek_chapter_context
from packages.core.context_engine.cache import (
    _fingerprint_author_intent,
    _fingerprint_writing_bible,
)
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.project.models import ProjectCreate, ProjectUpdate
from packages.domain.project.service import ProjectService, _coerce_writing_bible

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_BIBLE = "铁律：无 CP；第一位面＝现代都市；系统只做资源方，禁规则方。"
_DELTA = "本章价签只给价格数字。"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目", writing_bible: str | None = None) -> str:
    """插入项目行；``writing_bible`` 列缺失（模拟 0029 前的极老库）时自动跳过该列。"""
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        has_bible = "writing_bible" in {
            r["name"] for r in conn.execute("PRAGMA table_info(projects)")
        }
        if has_bible:
            sql = (
                "INSERT INTO projects (project_id, name, premise, genre, target_words, "
                "status, writing_bible, created_at, updated_at) VALUES "
                "(?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?, ?)"
            )
            params: tuple = (pid, name, writing_bible, now, now)
        else:
            sql = (
                "INSERT INTO projects (project_id, name, premise, genre, target_words, "
                "status, created_at, updated_at) VALUES "
                "(?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)"
            )
            params = (pid, name, now, now)
        conn.execute(sql, params)
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
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, created_at, updated_at) VALUES "
            "(?, ?, ?, 'C', ?, 'PLANNED', 'VISIBLE', ?, ?)",
            (cid, pid, number, plan_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _set_bible(db_path: Path, pid: str, value: str | None) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET writing_bible = ? WHERE project_id = ?", (value, pid),
        )
        conn.commit()
    finally:
        conn.close()


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    """统计 module.attr 的真实调用次数（命中返回深拷贝 ⇒ 不能用对象身份判定）。"""
    counter = {"n": 0}
    real = getattr(module, attr)

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


# ---------------------------------------------------------------------------
# 1. 领域服务往返（create / update / get / list）
# ---------------------------------------------------------------------------


def test_create_persists_writing_bible(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = ProjectService(db_path)

    created = svc.create(ProjectCreate(name="书1", writing_bible=f"  {_BIBLE}  "))

    assert created["writing_bible"] == _BIBLE  # 首尾空白被归一
    assert svc.get(created["project_id"])["writing_bible"] == _BIBLE
    assert svc.list()[0]["writing_bible"] == _BIBLE
    # DB 里也是非空文本（不是 "" 之类的中间形态）
    conn = get_connection(db_path)
    try:
        raw = conn.execute(
            "SELECT writing_bible FROM projects WHERE project_id = ?",
            (created["project_id"],),
        ).fetchone()["writing_bible"]
    finally:
        conn.close()
    assert raw == _BIBLE


def test_update_sets_clears_and_preserves_writing_bible(tmp_path: Path):
    """PATCH 三态：显式文本写入 / 显式 null 清除 / 省略保留原值。"""
    db_path = _fresh_db(tmp_path)
    svc = ProjectService(db_path)
    pid = svc.create(ProjectCreate(name="书1"))["project_id"]
    assert svc.get(pid)["writing_bible"] is None

    # 显式文本 → 写入
    assert svc.update(pid, ProjectUpdate(writing_bible=_BIBLE))["writing_bible"] == _BIBLE

    # 省略该字段 → 保留原值（不得被 None 默认值清掉）
    assert svc.update(pid, ProjectUpdate(name="书1（改名）"))["writing_bible"] == _BIBLE

    # 显式 null → 清除
    assert svc.update(pid, ProjectUpdate(writing_bible=None))["writing_bible"] is None
    assert svc.get(pid)["writing_bible"] is None

    # 空白串与显式 null 同义（清除）
    svc.update(pid, ProjectUpdate(writing_bible=_BIBLE))
    assert svc.update(pid, ProjectUpdate(writing_bible="   "))["writing_bible"] is None


def test_coerce_writing_bible_normalizes_blank_and_dirty_types():
    assert _coerce_writing_bible(None) is None
    assert _coerce_writing_bible("") is None
    assert _coerce_writing_bible(" \n\t ") is None
    assert _coerce_writing_bible(" 无 CP ") == "无 CP"
    # 内部换行 / 多段原样保留
    assert _coerce_writing_bible("甲\n\n乙") == "甲\n\n乙"
    # 脏数据（非字符串）→ None，不炸响应模型
    assert _coerce_writing_bible(123) is None
    assert _coerce_writing_bible({"a": 1}) is None


# ---------------------------------------------------------------------------
# 2. 解析单点：resolve_author_intent 的优先级契约
# ---------------------------------------------------------------------------


def test_resolve_author_intent_concatenates_bible_then_delta():
    out = resolve_author_intent(_BIBLE, _DELTA)
    # 基线在前、增量在后（后置且更具体的指令优先级更高）
    assert out.index(_BIBLE) < out.index(_DELTA)
    # 冲突裁决方向明文写在增量段（不只靠顺序暗示）
    assert "以本节为准" in out
    # 确定性：同一输入恒得快照一样的文本（缓存键稳定性前提）
    assert resolve_author_intent(_BIBLE, _DELTA) == out


def test_resolve_author_intent_passthrough_when_only_one_side():
    assert resolve_author_intent(_BIBLE, None) == _BIBLE
    assert resolve_author_intent(_BIBLE, "") == _BIBLE
    assert resolve_author_intent(None, _DELTA) == _DELTA
    assert resolve_author_intent(None, None) == ""
    # 首尾空白归一（避免同一意图的两种写法算出两个键）
    assert resolve_author_intent(" " + _BIBLE + " ", None) == _BIBLE


# ---------------------------------------------------------------------------
# 3. 装配注入：writer（full / paged）与 director
# ---------------------------------------------------------------------------


def test_writer_payload_carries_bible_without_run_intent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, writing_bible=_BIBLE)
    cid = _insert_chapter(db_path, pid, 1)
    _cache_reset()

    full = build_writer_input(db_path, cid, {}, context_mode="full")
    assert full["author_intent"] == {"raw": _BIBLE}

    # 生产默认走 paged——两条路径都必须带上（否则默认路径静默丢失，即 F-10 形状）
    paged = build_writer_input(db_path, cid, {}, context_mode="paged")
    assert paged["author_intent"] == {"raw": _BIBLE}


def test_writer_payload_combines_bible_and_run_intent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, writing_bible=_BIBLE)
    cid = _insert_chapter(db_path, pid, 1)
    _cache_reset()

    out = build_writer_input(db_path, cid, {}, author_intent=_DELTA)
    raw = out["author_intent"]["raw"]
    assert _BIBLE in raw and _DELTA in raw
    assert raw.index(_BIBLE) < raw.index(_DELTA)


def test_director_payload_carries_bible(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, writing_bible=_BIBLE)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "g"}')
    _cache_reset()

    bare = build_director_input(db_path, pid, cid, "")
    assert bare["author_intent"]["raw"] == _BIBLE

    with_delta = build_director_input(db_path, pid, cid, _DELTA)
    raw = with_delta["author_intent"]["raw"]
    assert _BIBLE in raw and _DELTA in raw


def test_production_planner_and_preview_assembly_carry_bible(tmp_path: Path):
    """生产路径的装配点（director_planner 合并输入 / dry-run 预览）都看得见圣经。

    - ``build_director_planner_input`` 是 chapter-plan 工作流的真实装配入口
      （``build_director_input`` ∪ 规划期上下文），plan 端点走的就是它；
    - ``preview_context`` 是前端挂载章节详情页即自动打的 dry-run，L2 展示
      ``author_intent`` 条目——圣经必须一并可见（否则「设置了圣经」在预览里看不出来）。
    """
    from packages.core.context_engine.preview import preview_context
    from packages.workflows.chapter_plan.planner_input import build_director_planner_input

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, writing_bible=_BIBLE)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "g"}')

    _cache_reset()
    planner_payload = build_director_planner_input(db_path, pid, cid, _DELTA)
    assert _BIBLE in planner_payload["author_intent"]["raw"]
    assert _DELTA in planner_payload["author_intent"]["raw"]

    _cache_reset()
    preview = preview_context(db_path, pid, cid)
    l2 = next(layer for layer in preview["layers"] if layer["id"] == "L2")
    intent_items = [it for it in l2["items"] if it["kind"] == "author_intent"]
    assert intent_items, "preview L2 应展示 author_intent 条目"
    assert _BIBLE in intent_items[0]["name"]


# ---------------------------------------------------------------------------
# 4. 缓存键纪律：改圣经必须 miss（硬规则 2）
# ---------------------------------------------------------------------------


def test_writer_cache_miss_on_bible_change(tmp_path: Path, monkeypatch):
    """改 ``projects.writing_bible``（state_version 与章节都不动）→ 必须重新装配。"""
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, writing_bible=_BIBLE)
    cid = _insert_chapter(db_path, pid, 1)
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    first = build_writer_input(db_path, cid, {})
    assert first["author_intent"]["raw"] == _BIBLE
    assert calls["n"] == 1

    # 同一 chapter / 同一 state_version，只改圣经
    _set_bible(db_path, pid, "铁律：无 CP；第一位面＝民国上海。")
    second = build_writer_input(db_path, cid, {})

    assert calls["n"] == 2, "改圣经必须 miss（否则读到旧装配）"
    assert second["author_intent"]["raw"] == "铁律：无 CP；第一位面＝民国上海。"

    # 回到原值 → 命中自己的条目（不再是「改一次就永久失效」）
    _set_bible(db_path, pid, _BIBLE)
    third = build_writer_input(db_path, cid, {})
    assert calls["n"] == 2
    assert third["author_intent"]["raw"] == _BIBLE


def test_director_cache_miss_on_bible_change(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, writing_bible=_BIBLE)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "g"}')
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    assert build_director_input(db_path, pid, cid, "")["author_intent"]["raw"] == _BIBLE
    assert calls["n"] == 1

    _set_bible(db_path, pid, "铁律：无 CP；系统下线。")
    assert build_director_input(db_path, pid, cid, "")["author_intent"]["raw"] == "铁律：无 CP；系统下线。"
    assert calls["n"] == 2, "改圣经必须 miss（否则读到旧装配）"


def test_writer_cache_miss_on_bible_set_and_clear(tmp_path: Path, monkeypatch):
    """无圣经 → 有圣经 → 换圣经 → 清空：圣经两态与 None 三态互不命中。

    末步「清空」故意回到最初的 None 态：它必须命中最初那条条目（证明 None 与文本
    不共享键，且清除后读到的是**无**作者意图的装配，而不是残留的旧圣经）。
    """
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    assert "author_intent" not in build_writer_input(db_path, cid, {})          # #1 None 态
    _set_bible(db_path, pid, _BIBLE)
    assert build_writer_input(db_path, cid, {})["author_intent"]["raw"] == _BIBLE  # #2
    _set_bible(db_path, pid, "铁律：无 CP；系统下线。")
    assert build_writer_input(db_path, cid, {})[
        "author_intent"
    ]["raw"] == "铁律：无 CP；系统下线。"                                        # #3
    assert calls["n"] == 3

    _set_bible(db_path, pid, None)
    assert "author_intent" not in build_writer_input(db_path, cid, {})          # 命中 #1
    assert calls["n"] == 3, "清空后必须命中最初的 None 态条目（不得残留旧圣经）"


def test_fingerprint_writing_bible_distinguishes_none_empty_and_text():
    fp = _fingerprint_writing_bible
    assert fp(None) == "none"
    assert fp(None) == _fingerprint_author_intent(None)
    # None / 空串 / 非空文本三态互不相同（同款口径见 _fingerprint_author_intent）
    assert len({fp(None), fp(""), fp(_BIBLE)}) == 3
    # 同文本恒同指纹（确定性）
    assert fp(_BIBLE) == fp(_BIBLE)


# ---------------------------------------------------------------------------
# 5. 回归：无圣经项目行为与改造前逐字一致
# ---------------------------------------------------------------------------


def test_project_without_bible_behaves_exactly_as_before(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)  # writing_bible = NULL
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "g"}')
    _cache_reset()

    # 无圣经 + 无运行意图 → payload 不出现该键（既有「缺省不出现键」纪律）
    assert "author_intent" not in build_writer_input(db_path, cid, {})
    assert build_director_input(db_path, pid, cid, "")["author_intent"]["raw"] == ""

    # 无圣经 + 有运行意图 → raw 逐字等于该意图（无 header / 无包装）
    _cache_reset()
    w = build_writer_input(db_path, cid, {}, author_intent=_DELTA)
    assert w["author_intent"] == {"raw": _DELTA}
    d = build_director_input(db_path, pid, cid, _DELTA)
    assert d["author_intent"] == {"raw": _DELTA, "structured": None}


def test_degraded_peek_still_carries_writing_bible(tmp_path: Path):
    """主 JOIN 失败走降级路径时，圣经必须照样读出来（极老库零行为突变）。

    构造：完整迁移的库（``writing_bible`` 有值）+ ``DROP TABLE reference_canons``
    ——主 ``_PEEK_CHAPTER_SQL`` 引用了该表 → ``OperationalError`` → 逐项降级读
    （与既有 ``test_peek_degrades_when_reference_canons_missing`` 同款构造手法）。
    降级路径若漏读 ``writing_bible``，作者的铁律会在这条路径上静默消失。

    只断言 peek 层：``reference_canons`` 缺失时连 writer 全量装配都会抛
    （``_reference_canon_excerpt`` 不吞该异常，属既有行为），本测试不覆盖那一层。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, writing_bible=_BIBLE)
    cid = _insert_chapter(db_path, pid, 1)
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE reference_canons")
        conn.commit()
    finally:
        conn.close()

    info = _peek_chapter_context(db_path, cid)
    # 确认确实走了降级路径（章节行仍读到，而不是空态 peek）
    assert info["found"] is True
    assert info["chapter_no"] == 1
    assert info["writing_bible"] == _BIBLE


def test_legacy_db_without_column_falls_back_to_no_bible(tmp_path: Path):
    """0029 未跑过的库（无 ``projects.writing_bible`` 列）→ 装配零行为突变。"""
    db_path = _fresh_db(tmp_path)
    conn = get_connection(db_path)
    try:
        conn.execute("ALTER TABLE projects DROP COLUMN writing_bible")
        conn.commit()
    finally:
        conn.close()

    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    info = _peek_chapter_context(db_path, cid)
    assert info["found"] is True
    assert info["project_id"] == pid
    assert info["writing_bible"] is None

    _cache_reset()
    assert "author_intent" not in build_writer_input(db_path, cid, {})
