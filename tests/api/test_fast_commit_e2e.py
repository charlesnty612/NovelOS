"""快通道端到端：写完直接提交（DRAFTED → COMMITTED，零人工往返）。

2026-10-14 新增（迁移 0031 ``projects.fast_mode``）。本文件钉三件事，
每件都挑了**可证伪**的断言：

1. **闸门真被拆掉**：``write(auto_commit=True)`` 之后不需要任何 resume，
   自动起 chapter-commit run 并走到 COMPLETED；全流程**零 PAUSED**。
2. **observer 没被一起砍**（关键取舍）：快通道只拆闸门不拆状态更新。
   断言 commit run 的节点行里有 COMPLETED 的 ``observer``，且 story_state
   真的推进了一版——**撤掉 observer 节点即红**。
3. **关掉快通道行为不变**：同样的 DRAFTED 章节走常规 commit 仍被拒
   （证明这不是「闸门被整体拆了」，而是有条件放行）。

测试模式沿用 ``tests/api/test_gate_confirm_e2e.py``（ASGITransport + 临时库 +
``mock_providers``）。

⚠️ **「不发真实模型调用」的确切含义**（2026-10-15 审查订正——原文档写
「``mock_providers`` 全程 mock，不发真实模型调用」，该表述在机制上为假）：

本文件的 mock 只覆盖 ``director`` / ``writer`` / ``observer`` 三个 agent。
``scene_planner``（chapter-write）、``summarizer``（chapter-commit）、
``critic``（chapter-review）**没有 mock 脚本**，它们会真的进
``ModelRouter.call_with_fallback``。拦住它们的**不是设计而是巧合**——临时库里
没有任何模型配置，router 在**构造 Provider 之前**就抛了
``ModelNotConfiguredError``，错误又被各节点的 degraded 分支吞掉，测试照样全绿。

现在这条不再靠巧合：``tests/api/test_fast_mode_review_gate.py::
test_e2e_db_cannot_reach_a_real_provider`` 断言临时库的 ``model_configs`` /
``model_profiles`` / ``capability_bindings`` 三表均为空——任何人将来在夹具里
播种一行模型配置，真实 Provider 就变得可构造，该不变量会先红。
同款形状在 ``test_gate_confirm_e2e.py`` 已存在，本文件是沿用而非新造。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.integration.test_v1_4_reference_and_revision import (
    _create_app,
    _director_script,
    _make_chapter,
    _make_project,
    _request,
    _sync_prompts,
    _wait_run_terminal,
)

# writer mock 的散文素材：**刻意全不重复**。
# 反例教材：早先用同一句 ×60，结果 trigram 重复率冲上 confirm 档，
# quality_gate 在 commit 节点**之前**就先拦下了 run —— 于是「对照组：DRAFTED 直提
# 应被状态机拒绝」根本走不到 commit 节点，测出来的是另一个原因。
# 教训与 AGENTS 坑区同款：**判别用例的夹具不能自带噪声**，否则失败点会漂移。
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
    "屋外的青石板被露水浸成了深色，踩上去不打滑。",
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
    "他用袖口擦掉桌上的一块水痕，露出底下粗糙的木纹。",
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
    "他把窗推开一条缝，让冷风进来，也让自己清醒一点。",
    "米缸边上搁着半袋豆子，是他上个月从集市上换回来的。",
    "屋后传来一声鸡叫，声音短促，像是被人掐住了嗓子。",
    "他终于站起来，把灯吹灭，屋里顿时暗成一片。",
    "出门之前他又回头看了一眼，确认门闩插好了。",
    "巷子里的青石板被踩得很滑，他走得不快，一步一步地稳着。",
    "路过井台的时候，他停下灌了两口水，凉得从喉咙一直滑到胸口。",
    "南边的天已经开始泛白，城墙的轮廓慢慢显出来。",
    "他走在路上，影子在身后拖得很长，一直拖到巷子拐角。",
    "城南的方向有炊烟，笔直地往上走，没有风。",
    "他一边走一边想，等这批炭卖完，就该置办点过冬的东西了。",
    "城门那边排起了队，都是等着出城去做活的人。",
    "守门的兵靠在墙根打哈欠，枪尖上的红缨被风吹得乱晃。",
    "他排在队伍里，前面是个挑担子的老头，担子两头是两头猪仔。",
    "轮到他的时候，兵丁扫了一眼他的路引，就挥手放行了。",
    "出城之后是土路，两边是刚收完的麦茬，一脚踩下去簌簌地响。",
    "他要去的地方在三十里外，走得快些，晌午能到。",
    "日头升起来的时候，路上的影子短了，人也跟着精神了些。",
)


def _build_prose() -> str:
    return "".join(_PROSE_SENTENCES)


def _writer_script(chapter_id: str, prose: str | None = None) -> list[str]:
    prose = prose if prose is not None else _build_prose()
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
    """observer mock：推进角色状态 → 逼出真实的 story_state 版本推进。

    **刻意不为空**：全空 delta 的 observer 也能让 commit 落地，那就测不出
    「observer 有没有被跳过」——而这恰恰是快通道最需要守住的那条边界。
    """
    return [
        json.dumps(
            {
                "character_changes": [
                    {
                        "change_id": "cc_fast_1",
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


async def _make_character(app, pid: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters", json={"name": "林昭"}
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


def _db_path(tmp_path: Path) -> str:
    from packages.core.config import Settings

    return str(Settings(data_dir=tmp_path, log_level="WARNING").db_path)


async def _runs_for_chapter(app, pid: str, cid: str) -> list[dict]:
    r = await _request(app, "GET", f"/api/projects/{pid}/runs")
    assert r.status_code == 200, r.text
    return [x for x in r.json() if x.get("chapter_id") == cid]


async def _wait_for_commit_run(app, pid: str, cid: str, *, timeout: float = 60.0) -> dict:
    """轮询直到出现 chapter-commit run 且走到终态。

    auto-commit 的子 run 由守护线程启动，HTTP 响应里**拿不到它的 run_id**
    （那是刻意的——调用方只有写作那一个 run_id），所以只能按 workflow 名找。
    """
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    last: list[dict] = []
    while time.monotonic() < deadline:
        last = await _runs_for_chapter(app, pid, cid)
        commit_runs = [x for x in last if x.get("workflow_name") == "chapter-commit"]
        if commit_runs:
            run = await _wait_run_terminal(
                app, commit_runs[0]["run_id"], expected=("COMPLETED", "FAILED", "PAUSED")
            )
            if run["status"] in ("COMPLETED", "FAILED", "PAUSED"):
                return run
        await asyncio.sleep(0.3)
    raise AssertionError(
        f"auto-commit 未在 {timeout}s 内起出 chapter-commit run；"
        f"现有 runs={[(x.get('workflow_name'), x.get('status')) for x in last]}"
    )


async def _plan(app, pid: str, cid: str, mock: dict) -> None:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters/{cid}/plan",
        json={"mock_providers": mock},
    )
    assert r.status_code == 201, r.text
    await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED",))


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_fast_mode_write_auto_commits_without_any_pause(tmp_path: Path):
    """主用例：开快通道 → 写完自动提交，全程零人工。"""
    app = _create_app(tmp_path)
    db_path = _db_path(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "fast project")
            cid = await _make_chapter(app, pid)
            char_id = await _make_character(app, pid)

            # 项目级开关走公开 API（同时验证 PATCH 真落库，不只是单测里的 service 调用）
            r = await _request(app, "PATCH", f"/api/projects/{pid}", json={"fast_mode": True})
            assert r.status_code == 200, r.text
            assert r.json()["fast_mode"] is True
            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.json()["fast_mode"] is True, "fast_mode 没落库"

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_script(cid, char_id),
            }
            await _plan(app, pid, cid, mock)

            # 写作 + 自动衔接提交
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock, "auto_commit": True},
            )
            assert r.status_code == 201, r.text
            write_run = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED")
            )
            assert write_run["status"] == "COMPLETED", write_run.get("error")

            commit_run = await _wait_for_commit_run(app, pid, cid)
            assert commit_run["status"] == "COMPLETED", commit_run.get("error")

            # --- 闸门真被拆掉：全程零 PAUSED
            runs = await _runs_for_chapter(app, pid, cid)
            assert not [x for x in runs if x["status"] == "PAUSED"], runs
            assert not any(
                x.get("workflow_name") == "chapter-review" for x in runs
            ), f"快通道不该起 chapter-review：{[(x['workflow_name']) for x in runs]}"

            # --- 章节确实定稿了
            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "COMMITTED", r.json()["status"]

            # --- observer 没被砍（关键取舍的看守）
            detail = await _request(app, "GET", f"/api/runs/{commit_run['run_id']}")
            assert detail.status_code == 200, detail.text
            node_rows = detail.json()["nodes"]
            by_id = {n["node_id"]: n for n in node_rows}
            assert "observer" in by_id, [n["node_id"] for n in node_rows]
            assert by_id["observer"]["status"] == "COMPLETED", by_id["observer"]
            # 高风险人工门也应被自动批准而非挂起
            assert "high_risk_approval" in by_id, [n["node_id"] for n in node_rows]
            assert by_id["high_risk_approval"]["status"] == "COMPLETED"

            return pid, cid, char_id, commit_run["run_id"]

    asyncio_run = __import__("asyncio").run(run())

    # --- story_state 真的推进了一版（observer 生效的直接后果）
    from packages.core.db import get_connection

    pid, cid, char_id, commit_run_id = asyncio_run
    conn = get_connection(db_path)
    try:
        states = conn.execute(
            "SELECT state_version FROM story_states WHERE project_id = ? ORDER BY state_version",
            (pid,),
        ).fetchall()
        commits = conn.execute(
            "SELECT author_approval_json FROM commits WHERE project_id = ?",
            (pid,),
        ).fetchall()
        char_state = conn.execute(
            "SELECT state_json FROM character_states WHERE character_id = ? "
            "ORDER BY state_version DESC LIMIT 1",
            (char_id,),
        ).fetchone()
    finally:
        conn.close()

    versions = [r["state_version"] for r in states]
    assert len(versions) >= 2, f"story_state 未推进（只有 {versions}）——observer 疑似被跳过"
    # 审批人不得记成 human
    assert commits, "commits 表无行——delta 没提交"
    approval = json.loads(commits[-1]["author_approval_json"])
    assert approval["approver"] == "system:fast_mode", approval
    assert "human" not in str(approval.get("approver")), "快通道不得伪造人工审批人"
    # 角色状态被 observer 改写 → 下一章能读到（这就是「不失忆」的含义）
    assert char_state is not None
    assert "城门" in str(char_state["state_json"]), char_state["state_json"]


def test_commit_from_drafted_still_rejected_when_fast_off(tmp_path: Path):
    """对照组：**快通道关闭时行为逐字不变**——DRAFTED 直提仍被拒。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "normal project")
            cid = await _make_chapter(app, pid)
            char_id = await _make_character(app, pid)

            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.json()["fast_mode"] is None, "新项目默认应为 NULL（未设置）"

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_script(cid, char_id),
            }
            await _plan(app, pid, cid, mock)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            write_run = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED")
            )
            assert write_run["status"] == "COMPLETED", write_run.get("error")

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.json()["status"] == "DRAFTED"

            # 没开 auto_commit ⇒ 不会自动起 commit run
            runs = await _runs_for_chapter(app, pid, cid)
            assert not [x for x in runs if x.get("workflow_name") == "chapter-commit"]

            # 手动 commit 仍应被原有状态机挡住
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/commit",
                json={"mock_providers": mock},
            )
            assert r.status_code == 201, r.text
            commit_run = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("FAILED", "COMPLETED", "PAUSED")
            )
            assert commit_run["status"] == "FAILED", commit_run.get("status")
            assert "chapter-review" in str(commit_run.get("error") or ""), commit_run.get("error")

    __import__("asyncio").run(run())


def test_auto_commit_requires_fast_mode_resolved(tmp_path: Path):
    """``auto_commit=True`` 但项目没开快通道 ⇒ 不自动提交。

    否则作者没同意的 DRAFTED 直提会被一个「顺手勾上的」auto_commit 静默触发。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            await _sync_prompts(app)
            pid = await _make_project(app, "auto-only project")
            cid = await _make_chapter(app, pid)
            char_id = await _make_character(app, pid)

            mock = {
                "director": _director_script(cid),
                "writer": _writer_script(cid),
                "observer": _observer_script(cid, char_id),
            }
            await _plan(app, pid, cid, mock)

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/chapters/{cid}/write",
                json={"mock_providers": mock, "auto_commit": True},
            )
            assert r.status_code == 201, r.text
            write_run = await _wait_run_terminal(
                app, r.json()["run_id"], expected=("COMPLETED", "FAILED")
            )
            assert write_run["status"] == "COMPLETED", write_run.get("error")

            import asyncio

            # 给守护线程一点时间，确认它确实没起 commit run
            await asyncio.sleep(3.0)
            runs = await _runs_for_chapter(app, pid, cid)
            assert not [
                x for x in runs if x.get("workflow_name") == "chapter-commit"
            ], f"项目未开快通道却起了 commit run：{runs}"

    __import__("asyncio").run(run())
