"""测试侧异步化适配：轮询 helper + 自动注入 helper 到被破坏的测试文件。

被破坏测试文件的特征：POST /plan|write|review|commit 后续断言
``status == "COMPLETED" / "PAUSED"``。本模块把这类断言改写为「轮询等终态 +
重读 DB 拿真实 status」。

使用方式：python tests/apply_async_patch.py

⚠️ **幂等守卫（2026-10-24 新增）**：第 5 步「POST 后自动插一条 wait」在**每次运行**
都会再插一遍，而「同一 run 连等多次」的第 2、3 次等待恒立即返回（第 1 次已等到终态）
⇒ 重复运行会在被改造的文件里叠加出成片的冗余调用。实测
``tests/workflow/test_chapter_pipelines.py`` 曾因此叠到 **62 条**「相邻同 run 同
expected」的恒等重复等待（52 条是同参数的「终态」等待三连，另 10 条是
``r2 = await _wait_run_terminal(app, r2["run_id"], ...)`` 的同义改写形）。
⇒ 第 5 步现在带幂等守卫：**插入前先看该 POST 块之后是否已经在等同一个 run**，是则
跳过插入（详见 ``_already_waits_same_run``）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def _imports_wait_helper(text: str) -> bool:
    """模块是否从别处 import 了 ``_wait_run_terminal``（含多行括号 import 块）。

    用 AST 而不是正则：``from x import (\\n  a,\\n  _wait_run_terminal,\\n)`` 这种
    多行 import 块用正则要跨行匹配，极易漏判 ⇒ 漏判的代价是**注入第二份同名
    helper**（后者遮蔽前者，静默改口径）。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(alias.name == "_wait_run_terminal" for alias in node.names):
                return True
    return False

# 需要注入到测试文件顶部的 helper 代码
HELPER_PY = '''
# 异步化适配（Sprint P0）：轮询 run 终态 + 重读 GET /runs 拿真实 status / pause_payload
async def _get_run_via_http(app, run_id: str) -> dict | None:
    import httpx
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        r = await client.get(f"/api/runs/{run_id}")
    finally:
        await client.aclose()
    if r.status_code == 404:
        return None
    return r.json()


async def _wait_run_terminal(app, run_id: str, *, expected=("COMPLETED", "PAUSED", "FAILED"), timeout: float = 60.0) -> dict:
    """轮询直到 run.status ∈ expected；返回最终 run dict。

    SQLite 跨连接视角 + 后台线程落库时延：单节点 mock 流程通常 < 1s 跑完，
    但 polling 必须等到节点行 FAILED/COMPLETED 也写入——轮询间隔 0.2s 足以。
    """
    import asyncio, time
    deadline = time.monotonic() + timeout
    last_run = None
    while time.monotonic() < deadline:
        run = await _get_run_via_http(app, run_id)
        last_run = run
        if run is None:
            raise AssertionError(f"run {run_id} disappeared")
        if run["status"] in expected:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach {expected} within {timeout}s (last={last_run['status']!r})")
'''


# 替换规则：(正则模式, 替换函数)
def make_replacements(file_text: str, varname: str) -> str:
    """针对单变量 varname（"plan_run" / "write_run" / "review_resp" / "final" / "commit_resp"）做替换。"""
    # 模式：assert varname["status"] == "COMPLETED"
    # 替换为：varname = await _wait_run_terminal(app, varname["run_id"], expected=("COMPLETED",))
    text = file_text
    text = re.sub(
        rf'assert {re.escape(varname)}\["status"\] == "COMPLETED", {re.escape(varname)}\n',
        f'{varname} = await _wait_run_terminal(app, {varname}["run_id"], expected=("COMPLETED",))\n',
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        rf'assert {re.escape(varname)}\["status"\] == "PAUSED", {re.escape(varname)}\n',
        f'{varname} = await _wait_run_terminal(app, {varname}["run_id"], expected=("PAUSED",))\n',
        text,
        flags=re.MULTILINE,
    )
    return text


def patch_file(path: Path) -> bool:
    """给测试文件注入 helper + 替换断言；返回是否改动。"""
    text = path.read_text(encoding="utf-8")
    original = text
    changed = False

    # 检查 helper 定义是否存在（不是引用）
    has_helper_def = bool(re.search(r"^async def _wait_run_terminal\(", text, re.MULTILINE))
    # ⚠️ 2026-10-24：拆分后的文件可能**从别的模块 import** 该 helper
    # （如 tests/workflow/test_chapter_pipelines_auto_revise.py 从 .test_chapter_pipelines
    # import _wait_run_terminal）⇒ 已有 import 时不得再注入一份本地定义，
    # 否则同模块里会出现两份同名 helper（后者遮蔽前者，静默改口径）。
    if not has_helper_def:
        has_helper_def = _imports_wait_helper(text)

    # 1) 注入 helper：找到**模块顶层** import 块的最后一行，插入**到下一个空行之后**（不破坏 def）
    if not has_helper_def:
        lines = text.splitlines(keepends=True)
        # 找到最后一个顶层 import 块结束位置——只在遇到 def/class/async def 时停止
        last_import_end = -1
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if line.startswith("import ") or line.startswith("from "):
                last_import_end = i
                j = i
                # 处理 from xxx import ( ... ) 多行块
                while j < len(lines) - 1:
                    s = lines[j].rstrip()
                    if "(" in s and ")" not in s:
                        while j < len(lines) - 1 and ")" not in lines[j]:
                            j += 1
                            last_import_end = j
                        break
                    if s.endswith(",") or s.endswith("\\"):
                        last_import_end = j + 1
                        j += 1
                    else:
                        break
                i = j + 1
            elif stripped == "":
                # 空行：跳过
                i += 1
            elif stripped.startswith("def ") or stripped.startswith("async def ") or stripped.startswith("class ") or stripped.startswith("@"):
                # 遇到模块顶层 def/class/装饰器：终止
                break
            else:
                # docstring 或其他注释行：跳过
                i += 1
        if last_import_end >= 0:
            # 跳过空行，停在第一个非空行（def/class）之前
            insert_at = last_import_end + 1
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            lines.insert(insert_at, "\n" + HELPER_PY)
            text = "".join(lines)
            changed = True

    # 2) 替换 status 断言（按变量名）
    for v in ("plan_run", "write_run", "review_resp", "final", "commit_resp", "paused", "review_run", "first_review_run_id", "second_review_run_id", "paused2", "paused_resp", "review_data"):
        text_new = make_replacements(text, v)
        if text_new != text:
            text = text_new
            changed = True

    # 2b) 泛化：任意 X["status"] == "COMPLETED" / "PAUSED" / "FAILED"（X 在语句前是 dict 含 "run_id"）
    # 模式：assert X["status"] == "TARGET"
    # 替换为：X = await _wait_run_terminal(app, X["run_id"], expected=("TARGET",))
    def _replace_any_var_status(match):
        indent = match.group(1)
        var = match.group(2)
        target = match.group(3)
        return (
            f'{indent}{var} = await _wait_run_terminal(app, {var}["run_id"], expected=("{target}",))\n'
        )

    text = re.sub(
        r'(^[ \t]*)assert\s+(\w+)\["status"\] == "(COMPLETED|PAUSED|FAILED)"(?:, \w+)?\n',
        _replace_any_var_status,
        text,
        flags=re.MULTILINE,
    )

    # 3) 通用替换：r.json()["status"] == "COMPLETED/PAUSED/FAILED"
    # 保留原缩进（取整行的 leading whitespace）
    def _replace_assert_status(match):
        indent = match.group(1)
        target = match.group(2)
        return (
            f'{indent}r2 = await _wait_run_terminal(app, r.json()["run_id"], expected=("{target}",))\n'
            f'{indent}assert r2["status"] == "{target}"\n'
        )

    text = re.sub(
        r'(^[ \t]*)assert r\.json\(\)\["status"\] == "(COMPLETED|PAUSED|FAILED)"(?:, r\.json\(\))?\n',
        _replace_assert_status,
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r'(^[ \t]*)assert r\.status_code == 200 and r\.json\(\)\["status"\] == "(COMPLETED|PAUSED|FAILED)", r\.text\n',
        _replace_assert_status,
        text,
        flags=re.MULTILINE,
    )
    print('DEBUG: after first sub, has COMPLETED =', 'assert r.json()["status"] == "COMPLETED"' in text, file=__import__('sys').stderr)
    # 4) commit_resp["status"] == "COMPLETED" 这类已命名变量
    for v in ("commit_resp", "final_run", "review_run"):
        def _replace_var_status(match, _v=v):
            indent = match.group(1)
            target = match.group(2)
            return (
                f'{indent}{_v} = await _wait_run_terminal(app, {_v}["run_id"], expected=("{target}",))\n'
            )

        text = re.sub(
            rf'(^[ \t]*)assert {re.escape(v)}\["status"\] == "(COMPLETED|PAUSED|FAILED)", {re.escape(v)}\n',
            _replace_var_status,
            text,
            flags=re.MULTILINE,
        )

    # 5) 关键修复：在 /api/projects/{pid}/chapters/{cid}/plan|write|review|commit POST 后
    # 自动插入 wait_run_terminal，避免下一轮同 chapter POST 被并发防护拒绝（409）。
    # 匹配模式：r = await _request(app, "POST", f".../plan|write|review|commit", json=DICT)
    #           assert r.status_code == 201, r.text
    # 插在下一行（assert 后）。DICT 可以含逗号。
    #
    # ⚠️ 幂等守卫（2026-10-24）：本步骤**每次运行都会再插一遍**，而「同一 run 连等
    # 多次」的第 2、3 次等待恒立即返回 ⇒ 重复运行会在被改造的文件里叠加成片的冗余
    # 调用（实测 test_chapter_pipelines.py 曾叠到 62+ 条恒等重复等待）。⇒ 插入前先
    # 问一句「这个 POST 返回的 run，是不是已经有人在等了」，是则跳过。
    # 判定口径是「**同一个 run**」而非「expected 逐字相同」：两次等待等的是同一个
    # run，第二次不构成任何额外保证，只会制造冗余；反之，等**别的** run 不算数，
    # 仍要插入。
    def _already_waits_same_run(match) -> bool:
        """该 POST 块之后是否已经在等「这个 POST 返回的那个 run」。

        向后看第一条「有意义的」行——跳过空行、``#`` 注释行、以及 ``X = r.json()``
        这类取 run_id 的绑定行——若它是针对同一 run 的 ``_wait_run_terminal`` 调用，
        返回 True（⇒ 不再插入）。
        """
        bound: set[str] = set()
        for line in match.string[match.end():].splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            bind = re.match(r"^([A-Za-z_]\w*)\s*=\s*r\.json\(\)(?:\[\"run_id\"\])?\s*$", stripped)
            if bind:
                bound.add(bind.group(1))
                continue
            call = re.match(
                r"^(?:([A-Za-z_]\w*)\s*=\s*)?await _wait_run_terminal\(\s*app,\s*(.+?)\s*,",
                stripped,
            )
            if not call:
                return False
            run_expr = call.group(2)
            return run_expr == 'r.json()["run_id"]' or run_expr in bound
        return False

    def _insert_wait_after_post(match):
        indent = match.group(1)
        url = match.group(2)
        block = (
            f'{indent}r = await _request(\n'
            f'{indent}    app, "POST", f"{url}",\n'
            f'{indent}    json={match.group(3)},\n'
            f'{indent})\n'
            f'{indent}assert r.status_code == 201, r.text\n'
        )
        if _already_waits_same_run(match):
            return block
        return block + (
            f'{indent}await _wait_run_terminal(app, r.json()["run_id"], expected=("COMPLETED", "PAUSED", "FAILED"))\n'
        )

    text = re.sub(
        r'(^[ \t]*)r = await _request\(\s*\n\s*app, "POST", f"(/api/projects/[^"]+/(?:plan|write|review|commit))",\s*\n\s*json=(\{[^}]*\}),\s*\n\s*\)\n\s*assert r\.status_code == 201(?:, r\.text)?\n',
        _insert_wait_after_post,
        text,
        flags=re.MULTILINE,
    )

    if text != original:
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


if __name__ == "__main__":
    files = [
        "tests/api/test_quality.py",
        "tests/integration/test_v1_4_reference_and_revision.py",
        # tests/unit/test_db_maintenance.py: 同步测试，不应加 async helper（patcher 会误改）
        "tests/workflow/test_chapter_commit_observer_parallel.py",
        "tests/workflow/test_chapter_commit_observer_retry.py",
        "tests/workflow/test_chapter_commit_observer_split.py",
        "tests/workflow/test_chapter_commit_observer_split_o3.py",
        "tests/workflow/test_chapter_pipelines.py",
        # 2026-10-24 从 test_chapter_pipelines.py 拆出；helper 从前者 import
        # （⇒ 第 1 步不会重复注入，见 _imports_wait_helper）。
        "tests/workflow/test_chapter_pipelines_auto_revise.py",
        "tests/workflow/test_chapter_plan_preserve_word_count.py",
        "tests/workflow/test_chapter_review_critic.py",
        "tests/workflow/test_chapter_write_revise_mode.py",
        "tests/workflow/test_chapter_write_scene_planner.py",
        "tests/workflow/test_checkpoint_exclude_promotion.py",
        "tests/workflow/test_human_node_pause_resume.py",
        "tests/workflow/test_model_overrides_passthrough.py",
        "tests/workflow/test_project_init.py",
        "tests/workflow/test_smoke_full_chain.py",
        "tests/workflow/test_workflow_resume_after_crash.py",
    ]
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"SKIP {f}")
            continue
        if patch_file(p):
            print(f"PATCHED {f}")
        else:
            print(f"UNCHANGED {f}")
