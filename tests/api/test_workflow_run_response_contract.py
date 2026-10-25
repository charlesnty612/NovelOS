"""workflows 主链端点的 ``response_model`` 契约测试（2026-10-15 批次（六）·G4）。

覆盖 control / runs / revise 三个子模块的 12 个高频端点（见 ``EXPECTED_*`` 常量与
``COVERED_ENDPOINTS``）。

建模前**先量再建**
------------------
`response_model` 会**过滤**未声明字段：少声明一个，响应就静默少一个，而用 ``>=``
断言的测试照样全绿。所以字段形状不是照源码读出来的，而是用**临时库 + mock provider**
跑通端点后 dump 实际键集得出的（2026-10-15 实测，方法：ASGITransport + 合成夹具，
下面 `_harvest` 一次跑完全部 12 个端点并把响应体收上来）。实测要点：

* 起跑 7 个端点（plan/write/review/commit/gate-revise/outline-batch/resume）**逐字同形
  3 键**：``run_id / status / current_node``，且 ``status`` 恒 ``"RUNNING"``；
* ``POST /projects/init`` **4 键**（多 ``project_id``，且可为 null），**PAUSED 时 5 键**；
* ``POST /runs/{id}/cancel`` **恒 2 键**——不为了凑字段硬造 ``current_node``；
* ``GET /runs/{id}`` **13 键**；PAUSED 时 **15 键**（多 ``pause_payload`` /
  ``stage_models``），而同一个响应里 ``chapter_id`` / ``error`` / ``ended_at`` 常常
  **键在、值为 null**；
* ``GET /projects/{pid}/init-status`` 2 键，``stages[]`` 每行 4 键；
* ``GET /chapters/{cid}/context-preview`` 7 键、``layers[]`` 每层 5 键，
  **但 ``items[]`` 的键集逐层不同**（见 `EXPECTED_PREVIEW_ITEM_CORE_KEYS`）。

判别设计（禁自指断言）
--------------------
本仓已实测过「自指断言恒真」：``assert set(body) == set(Model.model_fields)`` 在
「模型少声明一个字段」时**两侧同时变小、仍然相等**，断言恒真。故本文件的基准是
**硬编码的 `EXPECTED_*` 常量**——它独立于模型，也独立于响应，由后端行为决定。
模型字段集只是**另一条**与同一基准比对的线（`test_models_stay_in_sync_with_expectation`）。

⚠️ 而模型全开了 ``extra="allow"``，于是「删掉字段声明」在**响应里看不出来**
（未声明的键照样透传，2026-10-15 实测确认）。因此本文件**必须**另有一条断言 OpenAPI
里 schema 的 ``properties`` / ``required``
（`test_openapi_schema_properties_match_hardcoded_expectation`）——
那是唯一能抓到「声明丢失」的地方。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from packages.core.api.routers.workflows.models import (
    ContextPreview,
    ContextPreviewItem,
    ContextPreviewLayer,
    ProjectInitStage,
    ProjectInitStarted,
    ProjectInitStatus,
    RunCancelled,
    WorkflowRunDetail,
    WorkflowRunNode,
    WorkflowStartAccepted,
)
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection

from ._shared import _create_app

# ---------------------------------------------------------------------------
# 期望键集：**硬编码**，不写成 set(Model.model_fields)（自指断言恒真，见模块 docstring）
# ---------------------------------------------------------------------------

EXPECTED_START_KEYS = {"run_id", "status", "current_node"}
EXPECTED_INIT_KEYS = {"run_id", "status", "current_node", "project_id"}
EXPECTED_INIT_PAUSED_KEYS = EXPECTED_INIT_KEYS | {"pause_payload"}
EXPECTED_CANCEL_KEYS = {"run_id", "status"}
EXPECTED_INIT_STATUS_KEYS = {"stages", "has_any_data"}
EXPECTED_STAGE_KEYS = {"stage", "label", "done", "detail"}
EXPECTED_RUN_DETAIL_KEYS = {
    "run_id",
    "workflow_id",
    "workflow_name",
    "chapter_id",
    # 0037（2026-10-24 · C-1/C-2）：run 归属列，SELECT * 直出（0026 instance_id
    # 同款先例——WorkflowRunDetail extra="allow"，迁移加列响应就多一个键）。
    "project_id",
    "status",
    "current_node",
    "checkpoint_json",
    "error",
    "retry_count",
    "started_at",
    "ended_at",
    "instance_id",
    "nodes",
}
EXPECTED_RUN_DETAIL_PAUSED_KEYS = EXPECTED_RUN_DETAIL_KEYS | {"pause_payload", "stage_models"}
EXPECTED_NODE_KEYS = {
    "node_run_id",
    "run_id",
    "node_id",
    "agent_id",
    "status",
    "input_json",
    "output_json",
    "prompt_version",
    "model_id",
    "token_usage_json",
    "latency_ms",
    "error",
    "started_at",
    "ended_at",
}
EXPECTED_PREVIEW_KEYS = {
    "chapter_id",
    "project_id",
    "agents",
    "layers",
    "total_tokens",
    "token_budget",
    "within_budget",
}
EXPECTED_PREVIEW_LAYER_KEYS = {"id", "label", "token_estimate", "items", "truncated"}
#: ``items[]`` **逐层键集不同**（L1 带注入策略标记、L2 带 beats 计数），只有这三键
#: 是所有 kind 共有的——故只把这三项建模、其余交给 ``extra="allow"``。
EXPECTED_PREVIEW_ITEM_CORE_KEYS = {"kind", "id", "name"}
#: 实测三层各自出现过的专属键（**不是**每个 item 都有；用于确认 extra 透传没被关掉）。
EXPECTED_PREVIEW_ITEM_EXTRAS = {"injection", "source", "role", "status", "beats"}

#: 模型 → **全部已声明字段**（= OpenAPI schema 的 ``properties``）。
EXPECTED_PROPERTIES: dict[str, set[str]] = {
    "WorkflowStartAccepted": {"run_id", "status", "current_node"},
    "RunCancelled": {"run_id", "status"},
    "ProjectInitStarted": EXPECTED_INIT_PAUSED_KEYS,
    "ProjectInitStatus": EXPECTED_INIT_STATUS_KEYS,
    "ProjectInitStage": EXPECTED_STAGE_KEYS,
    "WorkflowRunDetail": EXPECTED_RUN_DETAIL_PAUSED_KEYS,
    "WorkflowRunNode": EXPECTED_NODE_KEYS,
    "ContextPreview": EXPECTED_PREVIEW_KEYS,
    "ContextPreviewLayer": EXPECTED_PREVIEW_LAYER_KEYS,
    "ContextPreviewItem": {"kind", "id", "name"},
}

#: 模型 → **必填子集**（= OpenAPI schema 的 ``required``）。``Optional[X]`` 不给默认值
#: ⇒ 必填可空（键恒在、值可为 null）；``= None`` ⇒ 可省略。两者不可混为一谈：前者
#: 声明在 ``properties`` 且进 ``required``，后者只在 ``properties``。
EXPECTED_REQUIRED: dict[str, set[str]] = {
    "WorkflowStartAccepted": {"run_id", "status", "current_node"},
    "RunCancelled": {"run_id", "status"},
    "ProjectInitStarted": EXPECTED_INIT_KEYS,
    "ProjectInitStatus": EXPECTED_INIT_STATUS_KEYS,
    "ProjectInitStage": EXPECTED_STAGE_KEYS,
    "WorkflowRunDetail": EXPECTED_RUN_DETAIL_KEYS,
    "WorkflowRunNode": EXPECTED_NODE_KEYS,
    "ContextPreview": EXPECTED_PREVIEW_KEYS,
    "ContextPreviewLayer": EXPECTED_PREVIEW_LAYER_KEYS,
    "ContextPreviewItem": {"kind", "id", "name"},
}

#: 本组建模的 11 个端点 → OpenAPI path。list-runs 端点（``GET /projects/{pid}/runs``）
#: **不在本组任务书范围内**，刻意未建模（它的键集比 run 详情多一个 ``label``）。
COVERED_ENDPOINTS = {
    ("/api/projects/init", "post"),
    ("/api/projects/{project_id}/chapters/{chapter_id}/plan", "post"),
    ("/api/projects/{project_id}/chapters/{chapter_id}/write", "post"),
    ("/api/projects/{project_id}/chapters/{chapter_id}/review", "post"),
    ("/api/projects/{project_id}/chapters/{chapter_id}/commit", "post"),
    ("/api/projects/{project_id}/chapters/{chapter_id}/gate-revise", "post"),
    ("/api/runs/{run_id}/resume", "post"),
    ("/api/runs/{run_id}/cancel", "post"),
    ("/api/projects/{project_id}/outline-batch", "post"),
    ("/api/projects/{project_id}/init-status", "get"),
    ("/api/runs/{run_id}", "get"),
    ("/api/chapters/{chapter_id}/context-preview", "get"),
}

#: ``GET /projects/{pid}/runs`` —— 原先作为「刻意不建模」的对照位，
#: 已于 2026-10-15 由 L2 组补齐为 ``list[WorkflowRunSummary]``，
#: 本文件对应的用例随之从「断言仍是裸数组」改写为「断言 items 有具名 $ref」。
LIST_RUNS_ENDPOINT = ("/api/projects/{project_id}/runs", "get")


# ---------------------------------------------------------------------------
# 夹具（自包含，不依赖 tests/integration 的 helper）
# ---------------------------------------------------------------------------


#: writer mock 的散文素材：**逐句互不重复**。反例教材见 AGENTS 坑区——早先用同一句
#: ×60，trigram 重复率冲上 confirm 档，quality_gate 在 commit 节点**之前**就拦下
#: run，于是对照组根本没走到被测节点，测出来的是另一个原因。
_PROSE_SENTENCES: tuple[str, ...] = (
    "破屋的灯还亮着，豆大的火苗在灯芯上打转，照出半面墙的裂纹。",
    "他把窗纸又糊了一遍，指腹顺着纸边压过去，纸上立刻洇出一小片潮。",
    "风从门缝里挤进来，带着巷子尽头炊烟的味道，说明那边已经有人起了。",
    "门槛上的青苔被夜露泡得发黑，他起身时顺手在衣摆上抹了两下。",
    "屋角那口缸裂了一道缝，水面始终降不到底，缸底露出半圈灰白的印子。",
    "灶台冷透了，铁锅底结着昨夜剩下的焦痕，硬得像一块薄石。",
    "他数了数米缸里的米，估摸着还能撑到开春，于是把袋口重新系紧。",
    "窗外的巷子窄，两个人并肩走就得侧身，墙根下积着昨夜的泥。",
    "远处传来打更的梆子声，三更过半，声音拖得很长才散。",
    "他把棉袄的领口翻起来，挡住灌进来的凉风，手却还是凉的。",
    "柴堆只剩细枝，掰断时发出干脆的响声，不像粗柴那样涩。",
    "水缸旁边摆着两只木桶，一只漏了底，他用泥巴糊了一层晾着。",
    "屋梁上垂下来的蛛丝沾了灰，他抬手拨开，看见一只空掉的茧壳。",
    "他想起白天路过城南时听说书人讲的旧事，结局他一直记不全。",
    "城门要到卯时才开，眼下过去，也不过是站在墙根下等。",
    "他把草席卷好塞进床底，打算天亮之后带着上路。",
    "墙角的药罐子空了，他拿指甲刮了刮底，还剩一点褐色的渣。",
    "夜风忽然大了一阵，房顶的瓦片响了几声，随后又归于安静。",
    "他伸手摸了摸床铺，褥子潮得发凉，索性坐回门槛上。",
    "巷口的更夫提着灯走过去，灯光把他的影子拉得很长。",
    "他想起肩上那道旧伤，阴天时会隐隐发酸，此刻倒没什么感觉。",
    "墙角堆着几只空坛子，坛口的封泥裂了，里面早就空干净。",
    "他翻出火折子，试了两次才点着，火苗一跳，把他半张脸照亮。",
    "屋外的土狗叫了两声，又忽然安静下去，像是看见了什么人。",
    "他把手伸进怀里，摸到那枚铜钱，边缘已经被磨得发亮。",
    "天边还是死黑一片，但东边的云已经薄了，隐约透出一点灰。",
    "他忽然想不起自己是从哪一年开始，把日子过成这样的。",
    "灯芯爆了一下，他赶紧用手拢住灯罩，怕火燎了房梁。",
    "地上的水渍是昨夜漏雨留下的，踩上去凉得刺脚。",
    "他把破凳子挪到门边，坐下时凳腿在砖上磨出一声尖响。",
    "巷子尽头传来铁匠敲打的声响，一下一下，很稳。",
    "天亮之后要去城南的布庄，把那件旧袄子换一件厚的。",
    "他低头数了数身上的铜板，够不够买盐，还得看布庄的价钱。",
    "门外的青石板被露水浸成了深色，踩上去不打滑。",
    "他把灯挑亮了些，顺手把桌角的茶碗翻过来扣着。",
    "风从瓦缝里钻出来，带着一股子冷，像是要把夜里最后一点暖意都带走。",
    "远处有孩子哭了两声，很快被人哄住了，声音断在那里。",
    "他在心里把今天的次序排了一遍，挑水，扫街，去布庄，回来劈柴。",
    "床底下那块砖有点松，他记得小时候曾经从那儿摸出过一枚铜板。",
    "天边那点灰意慢慢洇开，像是有人在云后面泼了一笔淡墨。",
    "他站起身，膝盖发出一声轻响，骨头缝里的凉气也跟着散了。",
    "灯油快见底了，他往里续了一点，火苗重新立直。",
    "巷口的狗又叫了一声，这回是朝远处叫的。",
    "他记得城南那家布庄的掌柜姓周，话不多，手上却实在。",
    "屋顶那片瓦松了很久，一直没换，昨夜又漏了一小股水。",
    "他用袖口擦掉桌上的一道水痕，露出底下粗糙的木纹。",
    "门外的土路上有车辙印，是昨夜运炭的车留下的。",
    "他忽然想去看看城南那口井，这个念头冒出来就压不住了。",
    "屋里没有钟，只有靠看窗纸的颜色来估时间。",
    "他抄起靠在墙边的扁担，在手里掂了掂，还算趁手。",
    "檐下挂着的那串干辣椒红得发暗，摘下来一颗捏了捏，又挂回去。",
    "天色一点点亮起来，屋里的轮廓从黑暗里浮出来。",
    "他往灶膛里添了两根柴，用火镰打不着，最后是从灯上引的火。",
    "那件旧袄子搭在床头，袖口磨破了，露出里头的棉絮。",
    "他想起去年冬天也是在这屋里过的年，一个人。",
    "门外的脚步声近了，是隔壁的邻居出门挑水。",
    "他推开门，冷风灌进来，街上已经有人在扫昨夜落的叶。",
    "巷子口卖炊饼的吆喝了一声，声音撞在墙面上又弹回来。",
    "他数着自己的脚步，从家门口数到井台，正好是四十步。",
    "井绳放下去，桶底碰着水面，声音闷闷地传上来。",
    "他把水桶提上来，水面上浮着一片叶子，他用手指拨开。",
    "邻家的狗跟出来几步，见他不理，也就转回去了。",
    "他在井台边站了一会儿，等呼吸匀了才往回走。",
    "回去的路上他绕去了柴房，摸了摸昨晚码好的那捆干柴。",
    "回到屋里，他把湿衣裳换下来，挂到横梁上晾着。",
    "日头渐高，屋梁的影子慢慢缩成一小块，压在墙角。",
    "他烧了水，就着半块冷饼把早饭对付了。",
    "门被推开，一个熟悉的声音喊他的名字。",
    "他应了一声，起身去开门，门外站着个提篮子的人。",
)


def _build_prose() -> str:
    return "".join(_PROSE_SENTENCES)


def _director_script(chapter_id: str) -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "director-plan.v1",
                "prompt_version": "director:v1",
                "chapter_id": chapter_id,
                "chapter_goal": "跑一遍 G4 契约夹具",
                "core_conflict": "无",
                "turning_point": "无",
                "expected_role": "setup",
                "key_beats": [
                    {
                        "beat_id": "beat_001",
                        "purpose": "测试",
                        "involved_characters": [],
                        "involved_locations": [],
                        "involved_hooks": [],
                        "involved_debts": [],
                        "risk_level": "LOW",
                        "narrative_question_served": "测试",
                    }
                ],
                "character_changes_planned": [],
                "information_releases": [],
                "hook_handling": [],
                "debt_handling": [],
                "proposed_new_entities": [],
                "deviations": [],
                "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
                "open_questions": [],
                "notes_for_planner": "",
            },
            ensure_ascii=False,
        )
    ]


def _writer_script(chapter_id: str) -> list[str]:
    prose = _build_prose()
    return [
        json.dumps(
            {
                "schema_version": "writer-output.v1",
                "prompt_version": "writer:v1",
                "chapter_id": chapter_id,
                "prose": prose,
                "self_report": {
                    "slots_filled": ["slot_001"],
                    "word_count": len(prose),
                    "scene_count": 1,
                    "deviations": [],
                    "forbidden_word_hits": [],
                    "self_check_notes": "",
                },
            },
            ensure_ascii=False,
        )
    ]


def _observer_script(chapter_id: str, character_id: str) -> list[str]:
    """observer 产出**非空** delta：全空 delta 的 observer 也能让 commit 落地。"""
    return [
        json.dumps(
            {
                "character_changes": [
                    {
                        "change_id": "cc_g4_1",
                        "op": "update",
                        "target_id": character_id,
                        "character_id": character_id,
                        "facet": "state",
                        "field": "state.location",
                        "before": "破屋",
                        "after": "城门",
                        "confidence": 0.9,
                        "evidence": {
                            "chapter_id": chapter_id,
                            "scene_id": None,
                            "excerpt": "他推开门，往城门去。",
                            "span": None,
                        },
                        "risk_level": "LOW",
                    }
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            },
            ensure_ascii=False,
        )
    ]




def _make_client(app) -> httpx.AsyncClient:
    """⚠️ **刻意不进入 lifespan**（与 ``tests/integration/test_v1_4_reference_and_revision.py``
    同款）。

    代价不是风格问题，是行为：lifespan 的 startup 会跑
    ``recover_interrupted_runs(db_path, current_instance_id())``，其收尾范围是
    ``instance_id = ? OR instance_id IS NULL``。若每个请求都新开一次 lifespan，被
    观察中的 RUNNING run 会在下一次轮询时被**启动自愈判死**成
    ``FAILED / 'interrupted: service restart killed worker thread'``——
    本文件第一版正是这样把 plan run 掐死的（表现为「90s 未到终态，最后 status=FAILED」，
    且日志里紧跟一条 ``startup recovered interrupted runs``）。

    所以 lifespan 只在 :func:`_harvest` 里进入**一次**，其余请求走裸 ASGITransport。
    ⇒ 实现收进共享 runner 的 ``client_ctx(enter_lifespan=False)``（同形）。
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as c:
        return await c.request(method, path, **kwargs)


async def _wait_run_terminal(
    app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 90.0
) -> dict:
    import time

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = await _request(app, "GET", f"/api/runs/{run_id}")
        last = r.json() if r.status_code == 200 else None
        if last and last["status"] in expected:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内到终态（最后 status={last and last.get('status')}）")


async def _seed_chapter(app, pid: str, number: int, title: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters", json={"number": number, "title": title}
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _harvest(tmp_path: Path) -> dict:
    """把 11 个端点各打一遍，把响应体收上来。

    全部走临时库 + ``mock_providers``。⚠️ **不发真实模型调用**的确切含义
    （与 ``test_fast_commit_e2e.py`` 同款订正）：mock 只覆盖 director / writer /
    observer 三个 agent；scene_planner / critic / summarizer 没有 mock 脚本，会真的
    进 ``ModelRouter.call_with_fallback``，拦住它们的是**临时库里没有任何模型配置**
    ——router 在**构造 Provider 之前**就抛 ``ModelNotConfiguredError``，错误又被各
    节点的 degraded 分支吞掉。`test_e2e_db_cannot_reach_a_real_provider` 把这条从
    「巧合」变成断言。
    """
    app = _create_app(tmp_path)
    # lifespan 只进这一次（理由见 _make_client 的 docstring：重复进入会把在跑的 run
    # 判死成 FAILED）
    async with app.router.lifespan_context(app):

        async def req(method: str, path: str, **kwargs) -> httpx.Response:
            async with _make_client(app) as client:
                return await client.request(method, path, **kwargs)

        db_path = str(Settings(data_dir=tmp_path, log_level="WARNING").db_path)

        r = await req("POST", "/api/agents/sync?docs_dir=docs/agents/prompts")
        assert r.status_code == 200, r.text

        r = await req("POST", "/api/projects", json={"name": "G4 契约"})
        assert r.status_code == 201, r.text
        pid = r.json()["project_id"]
        cid = await _seed_chapter(app, pid, 1, "第一章")
        cid_gate = await _seed_chapter(app, pid, 2, "第二章")
        cid_cancel = await _seed_chapter(app, pid, 3, "第三章")
        r = await req("POST", f"/api/projects/{pid}/characters", json={"name": "林昭"})
        assert r.status_code == 201, r.text
        char_id = r.json()["character_id"]

        out: dict = {"pid": pid, "cid": cid}

        # --- GET init-status
        r = await req("GET", f"/api/projects/{pid}/init-status")
        assert r.status_code == 200, r.text
        out["init_status"] = r.json()

        # --- GET context-preview
        r = await req("GET", f"/api/chapters/{cid}/context-preview")
        assert r.status_code == 200, r.text
        out["context_preview"] = r.json()

        mock = {
            "director": _director_script(cid),
            "writer": _writer_script(cid),
            "observer": _observer_script(cid, char_id),
        }

        # --- plan → write → review → commit（同一章，顺序即作者正常使用顺序）
        r = await req("POST", f"/api/projects/{pid}/chapters/{cid}/plan", json={"mock_providers": mock})
        assert r.status_code == 201, r.text
        out["plan"] = r.json()
        await _wait_run_terminal(app, out["plan"]["run_id"], expected=("COMPLETED",))

        r = await req("POST", f"/api/projects/{pid}/chapters/{cid}/write", json={"mock_providers": mock})
        assert r.status_code == 201, r.text
        out["write"] = r.json()
        await _wait_run_terminal(app, out["write"]["run_id"])

        r = await req("POST", f"/api/projects/{pid}/chapters/{cid}/review", json={"mock_providers": mock})
        assert r.status_code == 201, r.text
        out["review"] = r.json()
        review_run = await _wait_run_terminal(app, out["review"]["run_id"], expected=("PAUSED",))

        # --- PAUSED 的 run 详情（**resume 之前**取：resume 后就不再 PAUSED 了）
        r = await req("GET", f"/api/runs/{review_run['run_id']}")
        assert r.status_code == 200, r.text
        out["run_detail_paused"] = r.json()

        # --- POST resume（auto_revise_max=0：显式关掉自动改稿回路，让本用例无后台副作用）
        r = await req(
            "POST",
            f"/api/runs/{review_run['run_id']}/resume",
            json={"human_input": {"approved": True}, "auto_revise_max": 0},
        )
        assert r.status_code == 200, r.text
        out["resume"] = r.json()
        # 等被恢复的 review run 走到终态：resume 是异步的（立即返回 RUNNING），
        # 不同步等的话紧接着的 commit 会撞「同章已有活跃 run」的 409 —— 那是并发守卫
        # 正常工作，不是被测端点坏了。
        await _wait_run_terminal(app, review_run["run_id"])

        # --- 非 PAUSED 的 run 详情（plan run，已 COMPLETED）
        r = await req("GET", f"/api/runs/{out['plan']['run_id']}")
        assert r.status_code == 200, r.text
        out["run_detail_completed"] = r.json()

        # --- commit（章节此时仍 DRAFTED ⇒ run 会 FAILED，但 **201 响应体**与成败无关）
        r = await req("POST", f"/api/projects/{pid}/chapters/{cid}/commit", json={"mock_providers": mock})
        assert r.status_code == 201, r.text
        out["commit"] = r.json()

        # --- gate-revise：需要 plan_json.gate_blocked（quality_gate enforce 阻断的落点）
        conn = get_connection(db_path)
        try:
            conn.execute(
                "UPDATE chapters SET plan_json=? WHERE chapter_id=?",
                (
                    json.dumps(
                        {
                            "gate_blocked": {"gate": "block", "rule_ids": ["AI-CONTRACT-PROBE"]},
                            "revision_note": "按门禁建议改这一段",
                        }
                    ),
                    cid_gate,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        r = await req(
            "POST", f"/api/projects/{pid}/chapters/{cid_gate}/gate-revise", json={"mock_providers": mock}
        )
        assert r.status_code == 201, r.text
        out["gate_revise"] = r.json()

        # --- cancel：**直接播一行 RUNNING**，不真正起 workflow（确定性，且零 LLM）
        conn = get_connection(db_path)
        try:
            wf_id = conn.execute(
                "SELECT workflow_id FROM workflows WHERE name='chapter-plan'"
            ).fetchone()["workflow_id"]
            conn.execute(
                "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, current_node,"
                " checkpoint_json, error, retry_count, started_at, ended_at)"
                " VALUES ('wfr_g4_cancel', ?, ?, 'RUNNING', 'n1', '{}', NULL, 0, ?, NULL)",
                (wf_id, cid_cancel, "2026-10-15T00:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()
        r = await req("POST", "/api/runs/wfr_g4_cancel/cancel")
        assert r.status_code == 200, r.text
        out["cancel"] = r.json()
        r = await req("POST", "/api/runs/wfr_g4_cancel/cancel")
        assert r.status_code == 409, r.text
        out["cancel_conflict"] = r.json()

        # --- POST /api/projects/init：非 PAUSED（4 键）与 step_mode PAUSED（5 键）两条
        brief = {
            "genre": "玄幻",
            "logline": "少年得古籍，逆天改命",
            "platform": "起点",
            "target_words": 300000,
            "title": "契约探针书",
        }
        r = await req("POST", "/api/projects/init", json={"brief": brief})
        assert r.status_code == 201, r.text
        out["project_init"] = r.json()

        r = await req(
            "POST",
            "/api/projects/init",
            json={"brief": brief, "step_mode": True, "selected_stages": ["premise"]},
        )
        assert r.status_code == 201, r.text
        out["project_init_paused"] = r.json()

        # --- POST /api/projects/{pid}/outline-batch：**异步**起跑，响应恒 3 键 +
        #     status="RUNNING"（受理语义；后台线程可能已经跑完，真终态由轮询拿）。
        #     用**独立项目**跑：它会往库里插章，插进上面的项目会与既有章节号打架。
        r = await req("POST", "/api/projects", json={"name": "outline-batch 契约"})
        assert r.status_code == 201, r.text
        pid_ob = r.json()["project_id"]
        r = await req(
            "POST",
            f"/api/projects/{pid_ob}/outline-batch",
            json={
                "chapter_count": 1,
                "mock_providers": {
                    "outline_batch_planner": [
                        json.dumps(
                            {
                                "schema_version": 1,
                                "prompt_version": "outline_batch_planner:v1",
                                "chapter_seeds": [
                                    {
                                        "number": 1,
                                        "title": "契约探针章",
                                        "synopsis": "契约探针章的梗概：主角推门而入，只为确认门后无人。",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    ]
                },
            },
        )
        assert r.status_code == 201, r.text
        out["outline_batch"] = r.json()
        # 等后台线程走完（不留悬挂线程去写已回收的临时库）
        await _wait_run_terminal(app, out["outline_batch"]["run_id"])

    return out


@pytest.fixture(scope="module")
def harvested(tmp_path_factory) -> dict:
    return asyncio.run(_harvest(tmp_path_factory.mktemp("g4_contract")))


# ---------------------------------------------------------------------------
# 1) OpenAPI：schema 存在 + 11 个端点确实引用了它
# ---------------------------------------------------------------------------


def _resp_schema(openapi: dict, path: str, method: str) -> dict:
    op = openapi["paths"][path][method]
    # 端点可能声明 201（起跑）或 200（查询/恢复/取消）
    for code in ("200", "201"):
        if code in op["responses"]:
            return op["responses"][code]["content"]["application/json"]["schema"]
    raise AssertionError(f"{method.upper()} {path} 没有 200/201 的 json schema")


def test_all_covered_endpoints_declare_response_model(tmp_path: Path):
    """11 个端点**逐个**都有 response_model：schema 是具名 $ref 而不是裸 object。

    判别：撤掉任一 ``response_model=`` ⇒ 这里 $ref 消失（或退化成裸 object）⇒ 必红。
    """
    schema = _create_app(tmp_path).openapi()
    names = schema["components"]["schemas"]
    for name in EXPECTED_PROPERTIES:
        assert name in names, f"{name} 未进 OpenAPI components.schemas"
    for path, method in sorted(COVERED_ENDPOINTS):
        got = _resp_schema(schema, path, method)
        ref = got.get("$ref")
        assert isinstance(ref, str) and ref.endswith((
            "WorkflowStartAccepted", "RunCancelled", "ProjectInitStarted",
            "ProjectInitStatus", "WorkflowRunDetail", "ContextPreview",
        )), f"{method.upper()} {path} 的响应 schema 不是具名 $ref：{got}"


def test_list_runs_endpoint_is_now_modelled(tmp_path: Path):
    """``GET /projects/{pid}/runs`` 现已由 L2 组建模为 ``list[WorkflowRunSummary]``。

    ⚠️ 本用例在 2026-10-15 被**改写**：它原先是一条「对照位」，断言该端点
    **刻意保持裸数组**——那是 G4 组交付时的选择（当时判断「另建模型会让同一形状
    两份定义」，而列表行与详情行确实不同形）。随后 L2 组补齐了列表端点，
    该前提失效，此断言变成红的**陈旧前提**，不是缺陷信号。

    处置不是删掉它，而是**反转方向**：把一条「记录没做什么」的低价值对照位，
    换成一条真正守契约的正向断言——数组端点的 items 必须有具名 ``$ref``，
    否则前端拿到的仍是 ``Record<string, unknown>``（``list[dict]`` 在 OpenAPI 里
    items 是匿名 object，与未建模同形）。
    """
    schema = _create_app(tmp_path).openapi()
    got = _resp_schema(schema, *LIST_RUNS_ENDPOINT)
    assert got.get("type") == "array", f"列表端点 schema 应是 array，实际 {got}"
    items = got.get("items", {})
    ref = items.get("$ref", "")
    assert ref.endswith("/WorkflowRunSummary"), (
        f"list-runs 的 items 必须有具名 $ref（实测 {items}）——"
        "匿名 object 等价于未建模，前端只能拿 Record<string, unknown>"
    )


# ---------------------------------------------------------------------------
# 2) 响应键集 vs 硬编码期望（**不是** vs 模型字段集）
# ---------------------------------------------------------------------------


def test_start_endpoints_keys_match_hardcoded_expectation(harvested: dict):
    """7 个起跑/恢复端点逐字同形 3 键。"""
    for tag in ("plan", "write", "review", "commit", "gate_revise", "outline_batch", "resume"):
        body = harvested[tag]
        assert set(body) == EXPECTED_START_KEYS, (
            f"{tag} 键不符：多={set(body) - EXPECTED_START_KEYS} "
            f"少={EXPECTED_START_KEYS - set(body)}"
        )


def test_start_endpoints_status_is_running(harvested: dict):
    """``status`` 当前恒 RUNNING。

    模型里把它声明成 ``str`` 而不是 ``Literal["RUNNING"]``（理由见 models.py 的注释：
    **同族的 project-init 端点是同步启动**、实测会返回 PAUSED/COMPLETED，且它用另一个
    模型 ``ProjectInitStarted``），代价是 OpenAPI 不表达这个取值——所以在这里把该
    不变量钉住。
    """
    for tag in ("plan", "write", "review", "commit", "gate_revise", "outline_batch", "resume"):
        assert harvested[tag]["status"] == "RUNNING", f"{tag} 的 status 变了：{harvested[tag]}"
        assert harvested[tag]["current_node"] is None, f"{tag} 的 current_node 不再恒 null"


def test_cancel_response_is_exactly_two_keys(harvested: dict):
    """cancel 恒 2 键——**不为了凑字段硬造** current_node。"""
    assert set(harvested["cancel"]) == EXPECTED_CANCEL_KEYS, (
        f"多={set(harvested['cancel']) - EXPECTED_CANCEL_KEYS} "
        f"少={EXPECTED_CANCEL_KEYS - set(harvested['cancel'])}"
    )
    assert harvested["cancel"]["status"] == "CANCELLED"
    # 409 冲突体走 FastAPI 的 {detail}，不经 response_model
    assert set(harvested["cancel_conflict"]) == {"detail"}


def test_project_init_keys_4_and_5_when_paused(harvested: dict):
    """``POST /projects/init``：非 PAUSED 4 键、PAUSED 5 键（多 pause_payload）。

    这是 ``response_model_exclude_unset=True`` 的判别位：换成 exclude_none 会把
    「键在、值为 null」的 ``project_id`` / ``current_node`` 一起抹掉（4 键→2 键）；
    撤掉开关则 ``pause_payload`` 恒以 null 出现（非 PAUSED 时 4 键→5 键）。两个方向都红。
    """
    assert set(harvested["project_init"]) == EXPECTED_INIT_KEYS, (
        f"非 PAUSED 键不符：多={set(harvested['project_init']) - EXPECTED_INIT_KEYS} "
        f"少={EXPECTED_INIT_KEYS - set(harvested['project_init'])}"
    )
    paused = harvested["project_init_paused"]
    assert paused["status"] == "PAUSED", f"夹具没能造出 PAUSED 的 project-init：{paused}"
    assert set(paused) == EXPECTED_INIT_PAUSED_KEYS, (
        f"PAUSED 键不符：多={set(paused) - EXPECTED_INIT_PAUSED_KEYS} "
        f"少={EXPECTED_INIT_PAUSED_KEYS - set(paused)}"
    )
    assert isinstance(paused["pause_payload"], dict) and paused["pause_payload"]


def test_init_status_keys_match_hardcoded_expectation(harvested: dict):
    body = harvested["init_status"]
    assert set(body) == EXPECTED_INIT_STATUS_KEYS, (
        f"多={set(body) - EXPECTED_INIT_STATUS_KEYS} 少={EXPECTED_INIT_STATUS_KEYS - set(body)}"
    )
    assert len(body["stages"]) == 4, f"project-init 应恒 4 个环节，实际 {len(body['stages'])}"
    for row in body["stages"]:
        assert set(row) == EXPECTED_STAGE_KEYS, (
            f"stage 行键不符：多={set(row) - EXPECTED_STAGE_KEYS} "
            f"少={EXPECTED_STAGE_KEYS - set(row)}"
        )
    assert isinstance(body["has_any_data"], bool)
    assert [s["stage"] for s in body["stages"]] == ["premise", "world", "character", "outline"]


def test_run_detail_keys_match_hardcoded_expectation(harvested: dict):
    """非 PAUSED 的 run 详情恒 13 键、节点行恒 14 键。"""
    body = harvested["run_detail_completed"]
    assert body["status"] == "COMPLETED", f"夹具的 plan run 应 COMPLETED，实际 {body['status']}"
    assert set(body) == EXPECTED_RUN_DETAIL_KEYS, (
        f"多={set(body) - EXPECTED_RUN_DETAIL_KEYS} 少={EXPECTED_RUN_DETAIL_KEYS - set(body)}"
    )
    assert body["nodes"], "plan run 应至少有一个节点行"
    for node in body["nodes"]:
        assert set(node) == EXPECTED_NODE_KEYS, (
            f"节点行键不符：多={set(node) - EXPECTED_NODE_KEYS} "
            f"少={EXPECTED_NODE_KEYS - set(node)}"
        )
    # 键在、值为 null 的键**必须仍在**——这正是不能改用 exclude_none 的理由。
    assert "error" in body and body["error"] is None, "成功 run 的 error 应在场且为 null"
    assert "checkpoint_json" in body and isinstance(body["checkpoint_json"], dict)


def test_run_detail_paused_only_when_paused(harvested: dict):
    """PAUSED 的 run 详情恒 15 键（多 pause_payload / stage_models）。

    ⚠️ 这是本组**最容易被悄悄改掉**的一条：`GET /runs/{id}` 上那个
    ``response_model_exclude_unset=True`` 若被换成 ``exclude_none``（或撤掉），
    响应会变成 9 键（null 键被抹）或 15 键（条件键恒 null），本用例任一方向都红。
    """
    body = harvested["run_detail_paused"]
    assert body["status"] == "PAUSED", f"夹具的 review run 应 PAUSED，实际 {body['status']}"
    assert set(body) == EXPECTED_RUN_DETAIL_PAUSED_KEYS, (
        f"PAUSED 键不符：多={set(body) - EXPECTED_RUN_DETAIL_PAUSED_KEYS} "
        f"少={EXPECTED_RUN_DETAIL_PAUSED_KEYS - set(body)}"
    )
    assert isinstance(body["pause_payload"], dict) and body["pause_payload"]
    assert isinstance(body["stage_models"], dict), "stage_models 应是 dict（无调用时为 {}）"


def test_context_preview_keys_match_hardcoded_expectation(harvested: dict):
    """预览 7 键、每层 5 键；``items[]`` 只有 3 个共有键，其余是逐层专属。"""
    body = harvested["context_preview"]
    assert set(body) == EXPECTED_PREVIEW_KEYS, (
        f"多={set(body) - EXPECTED_PREVIEW_KEYS} 少={EXPECTED_PREVIEW_KEYS - set(body)}"
    )
    assert [lay["id"] for lay in body["layers"]] == ["L0", "L1", "L2"]
    assert isinstance(body["within_budget"], bool) and isinstance(body["total_tokens"], int)

    seen_extras: set[str] = set()
    seen_items = 0
    for lay in body["layers"]:
        assert set(lay) == EXPECTED_PREVIEW_LAYER_KEYS, (
            f"层 {lay['id']} 键不符：多={set(lay) - EXPECTED_PREVIEW_LAYER_KEYS} "
            f"少={EXPECTED_PREVIEW_LAYER_KEYS - set(lay)}"
        )
        for item in lay["items"]:
            seen_items += 1
            # 三个共有键**每个 item 都要有**（不是只在并集里出现过）
            missing = EXPECTED_PREVIEW_ITEM_CORE_KEYS - set(item)
            assert not missing, f"item 缺共有键 {missing}：{item}"
            seen_extras |= set(item) - EXPECTED_PREVIEW_ITEM_CORE_KEYS
    assert seen_items > 0, "夹具应至少产出一条预览条目"
    # 逐层专属键**必须透传**（extra="allow" 生效的直接证据）
    assert seen_extras, "items 里一条专属键都没出现——夹具太瘦，验不到 extra 透传"
    assert seen_extras <= EXPECTED_PREVIEW_ITEM_EXTRAS, (
        f"出现了未登记的 item 专属键 {seen_extras - EXPECTED_PREVIEW_ITEM_EXTRAS}；"
        f"若确有新 kind 的新键，请登记到 EXPECTED_PREVIEW_ITEM_EXTRAS 并同步 models.py"
    )


# ---------------------------------------------------------------------------
# 3) 模型字段集 vs 同一份硬编码期望（第二道保险，非自指）
# ---------------------------------------------------------------------------


def test_models_stay_in_sync_with_expectation():
    """模型字段集与**硬编码期望**一致。

    两侧（响应断言 / 模型断言）比的是同一个模型外的基准，不是彼此 ⇒ 任一侧漂移都红。
    """
    assert set(WorkflowStartAccepted.model_fields) == EXPECTED_START_KEYS
    assert set(RunCancelled.model_fields) == EXPECTED_CANCEL_KEYS
    assert set(ProjectInitStarted.model_fields) == EXPECTED_INIT_PAUSED_KEYS
    assert set(ProjectInitStatus.model_fields) == EXPECTED_INIT_STATUS_KEYS
    assert set(ProjectInitStage.model_fields) == EXPECTED_STAGE_KEYS
    assert set(WorkflowRunDetail.model_fields) == EXPECTED_RUN_DETAIL_PAUSED_KEYS
    assert set(WorkflowRunNode.model_fields) == EXPECTED_NODE_KEYS
    assert set(ContextPreview.model_fields) == EXPECTED_PREVIEW_KEYS
    assert set(ContextPreviewLayer.model_fields) == EXPECTED_PREVIEW_LAYER_KEYS
    # ContextPreviewItem 只建模共有键——逐层专属键交给 extra，**不**建模
    assert set(ContextPreviewItem.model_fields) == EXPECTED_PREVIEW_ITEM_CORE_KEYS


def test_extra_allow_keeps_undeclared_keys():
    """``extra="allow"``：未声明的键**不得**被裁掉。

    为什么必须直接对模型断言而不靠端点：本组 11 个端点当前**没有任何一个**返回未声明
    的键（实测键集与声明逐字相等），所以「关掉 extra」在端点响应里**看不出任何变化**
    —— 靠响应断言验它必然是空断言。合成一个未知键是最小的可判别手段（与批次（五）
    ``test_arc_response_contract.py`` 的 ``test_arc_extra_top_level_key_is_not_stripped``
    同款）。

    ⚠️ 另外这正是「删掉字段声明在响应里看不见」的根因（未声明的键会走 extra 通道
    透传），故下一条必须另断言 OpenAPI 的 properties。
    """
    dumped = WorkflowRunDetail(
        run_id="r", workflow_id="w", workflow_name=None, chapter_id=None,
        # 0037 加列后为必填可空（键恒在、值可 null），构造点显式给 None。
        project_id=None,
        status="RUNNING", current_node=None, checkpoint_json={}, error=None,
        retry_count=0, started_at="t", ended_at=None, instance_id=None, nodes=[],
        brand_new_column_from_migration_0032={"x": 1},
    ).model_dump()
    assert dumped["brand_new_column_from_migration_0032"] == {"x": 1}, "extra 被关掉了"

    dumped_start = WorkflowStartAccepted(
        run_id="r", status="RUNNING", current_node=None, brand_new_key="v"
    ).model_dump()
    assert dumped_start["brand_new_key"] == "v", "extra 被关掉了"

    dumped_item = ContextPreviewItem(kind="k", id="i", name="n", beats=3).model_dump()
    assert dumped_item["beats"] == 3, "items 的逐层专属键被裁掉了 ⇒ extra 被关掉了"


#: 名字 → 类。**从 ``EXPECTED_PROPERTIES`` 的键取全集**，不另列一份名单——
#: 另列就等于新增一处「靠手工保持同步」的地方，而那正是本缺陷的藏身处。
ALL_WORKFLOW_MODELS: dict[str, type] = {
    name: cls
    for name, cls in {
        "WorkflowStartAccepted": WorkflowStartAccepted,
        "RunCancelled": RunCancelled,
        "ProjectInitStarted": ProjectInitStarted,
        "ProjectInitStatus": ProjectInitStatus,
        "ProjectInitStage": ProjectInitStage,
        "WorkflowRunDetail": WorkflowRunDetail,
        "WorkflowRunNode": WorkflowRunNode,
        "ContextPreview": ContextPreview,
        "ContextPreviewLayer": ContextPreviewLayer,
        "ContextPreviewItem": ContextPreviewItem,
    }.items()
}


def test_every_workflow_model_declares_extra_allow():
    """**全部 10 个**模型都必须开 ``extra="allow"``——不是手挑的 3 个。

    ⚠️ 这条是被突变验证逼出来的（2026-10-16 批次八）：突变 ``G4M15`` 删掉
    ``RunCancelled`` 的 ``extra="allow"`` 后，**整份测试文件全绿**。

    真因不是断言写错，而是**上一条只枚举了 3 个模型**（``WorkflowRunDetail`` /
    ``WorkflowStartAccepted`` / ``ContextPreviewItem``）——手挑的那 3 个恰好各自有
    「合成一个未知键」的构造，而 ``RunCancelled`` 没有 ⇒ 它被漏在判别面之外。
    这与坑区「看守自己被悄悄缩小了执行域」「枚举判别面要枚举全」同源：
    **判别面写在测试里逐个点名，就等于给了它一个可以悄悄变小的形状**。

    改法不是补第 4 个模型，而是**改成对全集断言**：判据从「这 3 个模型透传了未知键」
    换成「**这 10 个模型都声明了 extra=allow**」。⇒ 新增模型时自动进判别面
    （只要它进了 ``EXPECTED_PROPERTIES``），而不是等人记得补一条。
    """
    assert set(ALL_WORKFLOW_MODELS) == set(EXPECTED_PROPERTIES), (
        f"模型名册与 EXPECTED_PROPERTIES 不一致："
        f"{set(ALL_WORKFLOW_MODELS) ^ set(EXPECTED_PROPERTIES)}"
    )
    missing = {
        name: m.model_config.get("extra")
        for name, m in ALL_WORKFLOW_MODELS.items()
        if m.model_config.get("extra") != "allow"
    }
    assert not missing, (
        f"这些模型没开 extra=allow，未声明的键会被静默裁掉（数据躺着，API 读不回来）：{missing}"
    )


def test_openapi_schema_properties_match_hardcoded_expectation(tmp_path: Path):
    """⚠️ 模型全开了 ``extra="allow"`` ⇒ 声明字段被删时**响应里照样有那个键**
    （走 extra 通道透传，2026-10-15 实测）。唯一能抓到「声明丢失」的地方是
    **OpenAPI 里 schema 的 properties / required**——它读的是模型声明本身。

    ``properties``（全部已声明字段）与 ``required``（其中必填的子集）**必须分开钉**：
    把两者当同一份集合，第一版就误把 ``pause_payload`` 这类「声明了但可省略」的键
    算进了 required，测试红了才暴露——它们是两个不同的集合。

    判别：从任一模型删掉一个字段声明 ⇒ ``properties`` 与硬编码期望不符 ⇒ 必红。
    """
    names = _create_app(tmp_path).openapi()["components"]["schemas"]
    for model_name, expected_props in EXPECTED_PROPERTIES.items():
        schema = names[model_name]
        props = set(schema.get("properties") or {})
        assert props == expected_props, (
            f"{model_name}.properties 与硬编码期望不符："
            f"多={props - expected_props} 少={expected_props - props}"
        )
        expected_required = EXPECTED_REQUIRED[model_name]
        required = set(schema.get("required") or [])
        assert required == expected_required, (
            f"{model_name}.required 不符：多={required - expected_required} "
            f"少={expected_required - required}"
        )
        assert expected_required <= props, f"{model_name} 的 required ⊄ properties"


def test_conditional_fields_are_optional_in_schema_not_required(tmp_path: Path):
    """两个条件键必须是 **optional**（不在 required 里），否则会被恒以 null 序列化。

    与上一条互为反面：上一条钉「声明齐了」，这一条钉「可空键别被误改成必填」。
    """
    names = _create_app(tmp_path).openapi()["components"]["schemas"]
    for model_name, optional in (
        ("ProjectInitStarted", {"pause_payload"}),
        ("WorkflowRunDetail", {"pause_payload", "stage_models"}),
    ):
        required = set(names[model_name].get("required") or [])
        assert not (optional & required), f"{model_name} 的 {optional} 被声明成必填了"


# ---------------------------------------------------------------------------
# 4) 夹具自身的两条不变量
# ---------------------------------------------------------------------------


def test_e2e_db_cannot_reach_a_real_provider(harvested: dict, tmp_path_factory):
    """临时库**无任何模型配置** ⇒ 真实 Provider 在构造之前就不可达。

    与 ``test_fast_commit_e2e.py`` / ``test_fast_mode_review_gate.py`` 同款。缺了它，
    「不发真实模型调用」就只是巧合而非保证：任何人往夹具里播种一行 model_configs，
    未被 mock 覆盖的 agent（scene_planner / critic / summarizer）就会真的发请求。
    """
    # reused fixture 的临时库已被 pytest 回收，这里重建一个同形态的空库来量
    tmp_path = tmp_path_factory.mktemp("g4_invariant")
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    conn = get_connection(str(settings.db_path))
    try:
        for table in ("model_configs", "model_profiles", "capability_bindings"):
            n = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            assert n == 0, f"{table} 非空 ⇒ 真实 Provider 可被构造，本夹具会发真实请求"
    finally:
        conn.close()
    assert harvested["pid"]  # 顺带确认夹具确实跑起来了
