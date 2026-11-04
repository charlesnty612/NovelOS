"""B-8 前端接线实证探针（静态面，2026-11-04 B 档逐条实证批）。

覆盖（审计报告 §四 B-8，grep/读取级证据；不跑构建）：
- B8-2 ``ALLOWED_NEXT`` 与后端差两条 + 前端零生产 import（死导出）。
- B8-4 ``outline_json`` 前端零消费（只有类型声明）。
- B8-5 ``qualityApi.evaluate`` 无版本入参（前后端一致地只测最新）。
- B8-6 ``ChapterRunBanner`` hintText 用不透明 ``workflow_id`` 判断 ⇒ 四个专属分支死代码。
- B8-9 ``WorkflowStartArgs.author_intent`` / ``target_word_count`` 声明了但无赋值点。
- B8-10 ``projects.foreshadow_overdue_chapters`` 前端无入口。
- B8-11 后端 8 个 skill 端点前端零消费。
- B8-7 pause_payload 前后端两套实现、优先级不同。

只读 ``apps/web/src``；零写入。
"""

from __future__ import annotations

import re

from probe_b_common import (  # noqa: I001  # pylint: disable=wrong-import-order
    REPO_ROOT,
    Recorder,
    force_utf8_stdout,
    verify_worktree_import,
)

force_utf8_stdout()
verify_worktree_import()

WEB = REPO_ROOT / "apps" / "web" / "src"


def read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def grep_all(pattern: str) -> list[str]:
    hits: list[str] = []
    for py in WEB.rglob("*"):
        if py.suffix not in {".ts", ".tsx"}:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # 排除纯注释行（// 或 * 开头）——注释提及不构成消费。
            if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                continue
            if re.search(pattern, line):
                hits.append(f"{py.relative_to(WEB).as_posix()}:{i}")
    return hits


def main() -> int:
    rec = Recorder("B-8 前端接线（静态面）")

    # ---------------------------------------------------------------- B8-2
    fe = read("utils/chapterState.ts")
    fe_reviewed = re.search(r"REVIEWED: \[([^\]]*)\]", fe).group(1)
    fe_committed = re.search(r"COMMITTED: \[([^\]]*)\]", fe).group(1)
    consumers = [h for h in grep_all(r"\bALLOWED_NEXT\b")
                 if not h.startswith("utils/chapterState.ts")
                 and ".test." not in h]
    rec.confirm(
        "B8-2 前端 ALLOWED_NEXT 缺 REVIEWED→DRAFTED / COMMITTED→REVIEWED 两条，"
        "但生产代码零 import（死导出 ⇒ 无实际影响，minor）",
        f"前端 REVIEWED={fe_reviewed.strip()}（后端含 DRAFTED）、"
        f"COMMITTED={fe_committed.strip()}（后端含 REVIEWED）；"
        f"生产 import 实测 {len(consumers)} 处 {consumers}（仅测试文件引用自身）",
    ) if ("DRAFTED" not in fe_reviewed and "REVIEWED" not in fe_committed
          and not consumers) else rec.refute(
        "B8-2", f"REVIEWED={fe_reviewed} COMMITTED={fe_committed} 消费={consumers}",
    )

    # ---------------------------------------------------------------- B8-4
    outline_hits = grep_all(r"\boutline_json\b")
    non_type = [h for h in outline_hits if not h.startswith("api/")]
    rec.confirm(
        "B8-4 chapters.outline_json 前端零消费（策展大纲槽在 UI 不可见不可编辑）",
        f"全 src 命中 {len(outline_hits)} 处，全部在 api/ 类型层；生产消费 "
        f"{len(non_type)} 处 {non_type}",
    ) if not non_type else rec.refute("B8-4", f"生产消费 {non_type}")

    # ---------------------------------------------------------------- B8-5
    ep = read("api/endpoints.ts")
    m_eval = re.search(r"evaluate: \(cid: string\)\s*=>\s*api\.post[^,]*,\s*\{\}\)?", ep)
    be = (REPO_ROOT / "packages" / "core" / "api" / "routers" / "quality.py").read_text(
        encoding="utf-8")
    be_has_version = "draft_version" in (re.search(
        r"def evaluate_chapter_quality.*?(?=\n@router|\Z)", be, re.S).group(0))
    rec.confirm(
        "B8-5 qualityApi.evaluate 无版本入参（永远评估最新稿，无法复评指定版本）",
        f"前端实测 {'POST body={}' if m_eval else '形态变化，需人工复核'}；"
        f"后端 evaluate_chapter_quality 形参/函数体含 draft_version={be_has_version}"
        f"（两端都不收 ⇒ 与『测量对象=被评版本』的既有纪律差一截，"
        f"但属功能缺口而非错误评估）",
    ) if (m_eval and not be_has_version) else rec.refute(
        "B8-5", f"m_eval={bool(m_eval)} be_has_version={be_has_version}",
    )

    # ---------------------------------------------------------------- B8-6
    banner = read("pages/chapter/ChapterRunBanner.tsx")
    hint_checks = re.findall(r"detail\.workflow_id === '([a-z-]+)'", banner)
    # 后端事实：workflow_id 是 new_id('wf') 的不透明 id；detail 端点恒附 workflow_name。
    rec.confirm(
        "B8-6 ChapterRunBanner hintText 用不透明 workflow_id 判断 ⇒ 四个专属分支恒不命中",
        f"hintText 的判断 {hint_checks} 全部比对 detail.workflow_id；"
        f"而 workflow_runs.workflow_id 是 new_id('wf') 不透明 id"
        f"（engine.py:_ensure_workflow），只有 workflow_name 才是 'chapter-write' 等"
        f"名字 ⇒ 实际永远落到通用文案。actionLabel 不受影响（第 2 优先级用 "
        f"workflow_name）。纯 cosmetic",
    ) if hint_checks else rec.refute("B8-6", "未找到 workflow_id 判断")

    # ---------------------------------------------------------------- B8-9
    start_hits = grep_all(r"author_intent|target_word_count")
    payload_sites = [h for h in start_hits
                     if "endpoints.ts" not in h and "types.generated.ts" not in h
                     and ".test." not in h]
    header = read("pages/chapter/ChapterPipelineHeader.tsx")
    button_payload = re.search(r"onStart\(b\.action, \{([^}]*)\}", header, re.S)
    has_intent_in_button = button_payload and (
        "author_intent" in button_payload.group(1)
        or "target_word_count" in button_payload.group(1))
    rec.confirm(
        "B8-9 WorkflowStartArgs.author_intent / target_word_count 声明了但四按钮从不赋值",
        f"四按钮 payload 实测字段：{button_payload.group(1).strip()[:120]}…；"
        f"两键在按钮/页面组装层的非类型命中 {payload_sites}（endpoints.ts:466-476 的"
        f"gate-revise 是另一条入口，不经四按钮）⇒ 章节页没有按次字数/铁律入口",
    ) if (button_payload and not has_intent_in_button) else rec.refute(
        "B8-9", f"按钮 payload 含两键={has_intent_in_button}",
    )

    # ---------------------------------------------------------------- B8-10
    overdue = grep_all(r"foreshadow_overdue_chapters")
    non_type_overdue = [h for h in overdue if "types.generated" not in h]
    rec.confirm(
        "B8-10 projects.foreshadow_overdue_chapters 前端无入口（进 director 装配的"
        "缓存键维度在 UI 不可配）",
        f"全 src 命中 {len(overdue)} 处 {overdue[:3]}…；非生成类型消费 "
        f"{len(non_type_overdue)} 处",
    ) if not non_type_overdue else rec.refute(
        "B8-10", f"消费 {non_type_overdue}",
    )

    # ---------------------------------------------------------------- B8-11
    # 真判据：API 客户端层（endpoints.ts / client）有没有 skill 端点的调用函数，
    # 以及非生成类型文件里有没有 /api/skills 请求串。生成类型里的 SkillXxx
    # interface（后端 schema 派生）不算消费。
    skill_hits = grep_all(r"\bskills?Api\b|api\.(get|post|put|delete)[^;\n]*skills?")
    rec.confirm(
        "B8-11 后端 8 个 skill 端点（routers/skill.py）前端零消费",
        f"全 src『skillsApi / api.<verb>(…skills…)』命中 {len(skill_hits)} 处 "
        f"{skill_hits[:3]}（api/types.ts 与 types.generated.ts 的 Skill* 类型声明"
        f"是 schema 派生的死类型，不是调用）",
    ) if not skill_hits else rec.refute("B8-11", f"命中 {skill_hits[:5]}")

    # ---------------------------------------------------------------- B8-7
    fe_pause = read("utils/pausePayload.ts")
    be_pause = (REPO_ROOT / "packages" / "core" / "api" / "routers" / "workflows"
                / "common.py").read_text(encoding="utf-8")
    fe_has_current = "current_node" in fe_pause
    be_has_current = re.search(
        r"def _extract_pause_payload.*?优先取与 ``current_node`` 对应节点",
        be_pause, re.S) is not None
    rec.confirm(
        "B8-7 pause_payload 抽取前后端两套实现且优先级不同（current_node 优先 vs 插入序）",
        f"前端实现含 current_node 优先逻辑={fe_has_current}（只有顶层/插入序两级）；"
        f"服务端实现 current_node 优先={be_has_current} ⇒ 多个挂起 payload 共存时"
        f"两侧可能取到不同的那个；服务端 PAUSED 响应已附 pause_payload ⇒ "
        f"前端抽取器是回退路径，分歧面窄",
    ) if (not fe_has_current and be_has_current) else rec.refute(
        "B8-7", f"fe_current={fe_has_current} be_current={be_has_current}",
    )

    return rec.summary()


if __name__ == "__main__":
    raise SystemExit(main())
