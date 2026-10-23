"""守护 ``scripts/measure_cache_key_perf.py`` 的**机制**（**不守护性能数值**）。

为什么只守机制
--------------
性能数字随机器 / 负载 / Python 版本变化，**写成断言就等于把机器噪声钉进 CI**
（AGENTS 坑区「自指断言恒真」的同族：断言一个环境相关常量，机器一变就红）。
⇒ 本文件只断言**可判定的机制事实**：

1. 脚本**默认不写仓库 ``data/``**（临时库落在仓库 ``tmp/`` 或系统临时目录）；
2. 显式传入**活库路径** ⇒ 拒绝并返回**非 0 退出码**；
3. 合成夹具确实造出 N 章、且**章号连续**（1..N）；
4. **命中与未命中两条路径都跑通**，且两者返回的 payload **内容不同**
   —— 这是防「脚本其实每次都 miss 却报告成 hit」的自指断言；
5. 脚本自报的命中/未命中**核对计数**与迭代数一致（量具自证）；
6. ``--db`` 的父路径**不可用**（不存在 / 是普通文件）⇒ 统一报错 + **退出码 2**
   + **零 traceback**（F-4：早先判据 ``exists()`` 对「父路径是文件」也为真 ⇒
   放行 ⇒ 建库时抛未捕获的 ``sqlite3.OperationalError`` ⇒ 栈甩到用户脸上）。

判别力说明
----------
第 4 条用**两条独立证据**（探针的 ``hit`` 标志 + payload 内容差异），
任何「只记 hit 不记 miss」或「只查存在不查值」的弱写法都过不了。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "measure_cache_key_perf.py"
DATA_DIR = REPO_ROOT / "data"

sys.path.insert(0, str(REPO_ROOT / "scripts"))


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _run_script(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd or REPO_ROOT),
    )


def _snapshot(path: Path) -> dict[str, tuple[int, int]] | None:
    """目录快照（相对路径 → (mtime_ns, size)）；目录不存在返回 None。"""
    if not path.exists():
        return None
    out: dict[str, tuple[int, int]] = {}
    for item in sorted(path.rglob("*")):
        try:
            st = item.stat()
        except OSError:  # pragma: no cover —— 竞态删除
            continue
        out[str(item.relative_to(path))] = (st.st_mtime_ns, st.st_size)
    return out


def _load_fixture_module():
    """导入测量脚本模块（脚本顶层不执行任何动作，只有 ``__main__`` 才跑）。"""
    import measure_cache_key_perf as perf

    return perf


# ---------------------------------------------------------------------------
# 1. 默认不写 data/
# ---------------------------------------------------------------------------


def test_default_run_uses_temp_db_and_never_touches_data_dir(tmp_path: Path) -> None:
    """默认跑一遍（无 ``--db``）：临时库必须在 ``tmp/`` 或系统临时目录，``data/`` 零变化。"""
    before = _snapshot(DATA_DIR)

    proc = _run_script("--chapters", "4", "--iterations", "2", "--warmup", "1",
                       "--assembly", "director", "--no-ablation")
    assert proc.returncode == 0, f"脚本失败：\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
    payload = json.loads(proc.stdout)

    db_path = Path(payload["results"][0]["db_path"]).resolve()
    tmp_root = (REPO_ROOT / "tmp").resolve()
    import tempfile

    temp_root = Path(tempfile.gettempdir()).resolve()
    assert tmp_root in db_path.parents or temp_root in db_path.parents, (
        f"默认临时库必须落在仓库 tmp/ 或系统临时目录，实际：{db_path}"
    )
    assert DATA_DIR.resolve() not in db_path.parents, f"默认路径不得指向 data/：{db_path}"

    after = _snapshot(DATA_DIR)
    assert after == before, "脚本运行期间 data/ 目录发生了变化（禁写活库）"


def test_explicit_tmp_db_path_is_honoured(tmp_path: Path) -> None:
    """``--db`` 指定的临时库路径被真正使用（且派生文件名带规模后缀）。"""
    base = tmp_path / "perf_probe.db"
    proc = _run_script("--db", str(base), "--chapters", "4", "--iterations", "2",
                       "--warmup", "1", "--assembly", "director", "--no-ablation")
    assert proc.returncode == 0, f"脚本失败：\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    payload = json.loads(proc.stdout)
    db_path = Path(payload["results"][0]["db_path"]).resolve()
    assert db_path.parent == tmp_path.resolve()
    assert db_path.stem.startswith("perf_probe")
    assert "ch4" in db_path.stem


# ---------------------------------------------------------------------------
# 2. 活库路径必须被拒绝
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "data/novelos.db",
        "data/anything_else.db",
    ],
)
def test_live_db_path_is_refused_with_nonzero_exit(rel: str) -> None:
    """传活库 / ``data/`` 下的路径 ⇒ 拒绝 + 非 0 退出码（不碰库、不产出数字）。"""
    target = REPO_ROOT / rel
    before = target.stat() if target.exists() else None
    proc = _run_script("--db", str(target), "--chapters", "2", "--iterations", "1",
                       "--warmup", "0")
    assert proc.returncode != 0, f"活库路径必须被拒绝，实际退出码 {proc.returncode}"
    assert proc.returncode == 3, f"拒绝退出码应为 3（可区分），实际 {proc.returncode}"
    assert "拒绝" in proc.stderr, f"stderr 应说明拒绝原因：{proc.stderr[-500:]}"
    if before is None:
        assert not target.exists(), "被拒绝的路径不得被创建"
    else:
        after = target.stat()
        assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size), (
            "被拒绝的活库文件不得被改动（mtime / size 变化）"
        )


def test_content_repo_path_is_refused() -> None:
    """内容仓 / 痕迹仓路径同样被拒绝（若这些目录在本机存在）。"""
    for sibling in ("NovelOS-Content", "NovelOS-Artifacts"):
        root = REPO_ROOT.parent / sibling
        if not root.exists():
            continue
        proc = _run_script("--db", str(root / "novels.db"), "--chapters", "2",
                           "--iterations", "1", "--warmup", "0")
        assert proc.returncode == 3, f"{sibling} 路径必须被拒绝，实际 {proc.returncode}"
        assert "拒绝" in proc.stderr


# ---------------------------------------------------------------------------
# 3. 合成夹具：N 章 + 章号连续
# ---------------------------------------------------------------------------


def test_synthetic_fixture_creates_n_chapters_with_continuous_numbers(tmp_path: Path) -> None:
    """夹具造出 N 章、章号 1..N 连续、无重复。"""
    from packages.core.db import get_connection

    perf = _load_fixture_module()
    db_path = tmp_path / "fixture.db"
    n_chapters = 9
    fx = perf.build_synthetic_fixture(db_path, n_chapters)

    assert len(fx["chapter_ids"]) == n_chapters
    conn = get_connection(str(db_path))
    try:
        rows = conn.execute(
            "SELECT number FROM chapters WHERE project_id = ? ORDER BY number ASC",
            (fx["project_id"],),
        ).fetchall()
    finally:
        conn.close()
    numbers = [int(r["number"]) for r in rows]
    assert numbers == list(range(1, n_chapters + 1)), f"章号应连续 1..{n_chapters}，实际 {numbers}"
    assert len(set(numbers)) == n_chapters, "章号不得重复"


def test_synthetic_fixture_uses_a_temp_db_not_the_live_one(tmp_path: Path) -> None:
    """夹具只往传入的路径写（且不落在 data/ 下）。"""
    perf = _load_fixture_module()
    db_path = tmp_path / "fixture2.db"
    perf.build_synthetic_fixture(db_path, 3)
    assert db_path.exists()
    assert DATA_DIR.resolve() not in db_path.resolve().parents


# ---------------------------------------------------------------------------
# 4. 命中 / 未命中两条路径都能跑通，且结果可区分
# ---------------------------------------------------------------------------


def test_hit_and_miss_paths_both_work_and_return_distinguishable_payloads(
    tmp_path: Path,
) -> None:
    """同一输入第二次必命中；改一个键维度后必未命中；两条路径的 payload **内容不同**。

    两条独立证据：
    - 探针的 ``hit`` 标志（True / False）——证明缓存状态真的变了；
    - payload 内容差异（章标题进了 payload）——证明不是同一份结果被重复报成两种。
    """
    from packages.core.context_engine import director_input

    perf = _load_fixture_module()
    db_path = tmp_path / "hitmiss.db"
    fx = perf.build_synthetic_fixture(db_path, 6)
    chapter_id = fx["target_chapter_id"]
    project_id = fx["project_id"]
    intent = "合成意图（测试用）"

    def _call() -> dict:
        return director_input.build_director_input(
            str(db_path), project_id, chapter_id, intent, target_word_count=2200,
        )

    with perf.AssemblyProbe(director_input) as probe:
        first_payload, first_seg = probe.call(_call)
        second_payload, second_seg = probe.call(_call)

    assert first_seg["hit"] is False, "首次调用应是未命中（缓存为空）"
    assert second_seg["hit"] is True, "同一输入的第二次调用必须是命中"
    assert first_payload == second_payload, "命中的结果应与首次装配内容一致（深拷贝）"

    # 改一个**进缓存键**的 DB 侧维度（chapters.title ⇒ title_fp）⇒ 必未命中
    perf._set_chapter_title(db_path, chapter_id, "改过的合成标题")
    with perf.AssemblyProbe(director_input) as probe:
        miss_payload, miss_seg = probe.call(_call)

    assert miss_seg["hit"] is False, "改了键维度后必须未命中"
    assert miss_payload != second_payload, "未命中的结果必须与命中的结果内容不同"
    assert miss_payload["chapter"]["title"] == "改过的合成标题"
    assert second_payload["chapter"]["title"] != "改过的合成标题"


# ---------------------------------------------------------------------------
# 5. 脚本自报的核对计数必须自洽
# ---------------------------------------------------------------------------


def test_script_reports_verified_hit_and_miss_counts(tmp_path: Path) -> None:
    """脚本自报「已核对命中数 / 未命中数」必须等于迭代数（量具自证，不是性能断言）。"""
    iterations = 3
    proc = _run_script("--chapters", "4", "--iterations", str(iterations), "--warmup", "1",
                       "--assembly", "director", "--no-ablation")
    assert proc.returncode == 0, f"脚本失败：\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    payload = json.loads(proc.stdout)
    result = payload["results"][0]["director"]

    hit = result["hit"]
    assert hit["verified_hits"] == iterations, "命中次数必须逐次核对且等于迭代数"
    assert hit["total"]["n"] == iterations
    miss = result["miss_db_dim"]
    assert miss["verified_misses"] == iterations, "未命中次数必须逐次核对且等于迭代数"
    assert miss["total"]["n"] == iterations
    param = result["miss_param_dim"]
    assert param["verified_misses"] == iterations


# ---------------------------------------------------------------------------
# 6. ``--db`` 父路径不可用 ⇒ 清晰报错 + 退出码 2 + 零 traceback（F-4）
# ---------------------------------------------------------------------------

#: 「父路径不可用」的统一报错前缀——两种形态（不存在 / 是普通文件）必须同前缀、同退出码。
_ERR_PARENT_PREFIX = "[错误] --db 的父目录"


def _db_artifacts_under(root: Path) -> list[Path]:
    """递归找出 ``root`` 下所有临时库残留（``*.db`` / ``*-wal`` / ``*-shm``）。"""
    if not root.exists():
        return []
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.name.endswith((".db", "-wal", "-shm"))
    )


def test_db_parent_is_a_regular_file_fails_cleanly_without_traceback(
    tmp_path: Path,
) -> None:
    """F-4 判别用例：``--db`` 的父路径是**普通文件** ⇒ 退出码 2 + 明确原因 + **零 traceback**。

    缺陷版判据是 ``base.parent.exists()``——对「父路径是个普通文件」同样为真 ⇒ 放行
    ⇒ 建库时抛未捕获的 ``sqlite3.OperationalError: unable to open database file``
    ⇒ 退出码 1 + 栈甩到用户脸上（用户看到的是「跑挂了」，不是「路径写错了」）。

    断言打在**真实行为**上：退出码 / 输出内容 / 落盘残留三头并查，
    不是「只查存在不查值」，也不是恒真断言。
    """
    parent_file = tmp_path / "i_am_a_file.txt"
    parent_file.write_text("not a directory\n", encoding="utf-8")
    before = parent_file.stat()

    proc = _run_script(
        "--db", str(parent_file / "perf.db"),
        "--chapters", "2", "--iterations", "1", "--warmup", "0",
        "--assembly", "director",
    )
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 2, (
        f"父路径是普通文件时必须以退出码 2 收场，实际 {proc.returncode}\n{combined[-2000:]}"
    )
    assert "Traceback" not in combined, f"不得把 traceback 甩给用户：\n{combined[-2000:]}"
    assert "OperationalError" not in combined, f"不得漏出底层 sqlite 异常：\n{combined[-2000:]}"
    assert proc.stderr.startswith(_ERR_PARENT_PREFIX), (
        f"stderr 应走统一的父目录报错路径：{proc.stderr[-500:]}"
    )
    assert "不是目录" in proc.stderr, f"stderr 应说清是哪种情况：{proc.stderr[-500:]}"
    assert str(parent_file) in proc.stderr, (
        f"stderr 应打印出问题的那个路径：{proc.stderr[-500:]}"
    )
    assert _db_artifacts_under(tmp_path) == [], "失败路径不得产出任何临时库残留"
    after = parent_file.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size), (
        "被当作父路径的普通文件不得被改动"
    )


def test_db_parent_missing_fails_cleanly_with_exit_code_2(tmp_path: Path) -> None:
    """反向不变量：父路径**不存在** ⇒ 同样退出码 2（防把这条判绿 / 改过头）。

    与 F-4 用例成对：既证「是文件」被拦，也证「不存在」没被改坏。
    """
    missing = tmp_path / "no_such_dir"
    proc = _run_script(
        "--db", str(missing / "perf.db"),
        "--chapters", "2", "--iterations", "1", "--warmup", "0",
        "--assembly", "director",
    )
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 2, (
        f"父路径不存在时必须以退出码 2 收场，实际 {proc.returncode}\n{combined[-2000:]}"
    )
    assert "Traceback" not in combined, f"不得把 traceback 甩给用户：\n{combined[-2000:]}"
    assert proc.stderr.startswith(_ERR_PARENT_PREFIX), (
        f"stderr 应走与「是文件」同一条报错路径：{proc.stderr[-500:]}"
    )
    assert "不存在" in proc.stderr, f"stderr 应说清是哪种情况：{proc.stderr[-500:]}"
    assert str(missing) in proc.stderr, (
        f"stderr 应打印出问题的那个路径：{proc.stderr[-500:]}"
    )
    assert not missing.exists(), "失败路径不得替你把目录建出来"
    assert _db_artifacts_under(tmp_path) == [], "失败路径不得产出任何临时库残留"


def test_db_parent_file_rejected_under_default_both_assembly(tmp_path: Path) -> None:
    """默认装配模式（``--assembly both`` ⇒ 父进程派生子进程测）下同样干净收场。

    防的是「只在单装配器分支判了、``both`` 分支漏判」这种半修：
    默认参数就是 ``both``，只测 ``--assembly director`` 等于没测默认路径。
    """
    parent_file = tmp_path / "i_am_a_file.txt"
    parent_file.write_text("not a directory\n", encoding="utf-8")

    proc = _run_script(
        "--db", str(parent_file / "perf.db"),
        "--chapters", "2", "--iterations", "1", "--warmup", "0",
    )
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 2, (
        f"默认装配模式下父路径是普通文件也必须退出码 2，实际 {proc.returncode}\n"
        f"{combined[-2000:]}"
    )
    assert "Traceback" not in combined, f"不得把 traceback 甩给用户：\n{combined[-2000:]}"
    assert proc.stderr.startswith(_ERR_PARENT_PREFIX), (
        f"stderr 应走统一的父目录报错路径：{proc.stderr[-500:]}"
    )
    assert _db_artifacts_under(tmp_path) == [], "失败路径不得产出任何临时库残留"


def test_db_parent_real_directory_still_runs_normally(tmp_path: Path) -> None:
    """防「过度拦截」：父路径是正常目录时脚本照常跑出结果（不是一律拒绝）。

    这里**完整跑一次**（2 章 / 1 次迭代 / 只测 director，实测约 0.7s），
    不用「只验前置校验」的近似写法——近似写法证明不了「正常路径没被误伤」。
    """
    proc = _run_script(
        "--db", str(tmp_path / "perf.db"),
        "--chapters", "2", "--iterations", "1", "--warmup", "0",
        "--assembly", "director", "--no-ablation",
    )
    assert proc.returncode == 0, (
        f"父路径是正常目录时必须照常跑通：\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    payload = json.loads(proc.stdout)
    assert payload["results"][0]["chapters"] == 2, "正常路径的规模点应如实报出"
    db_path = Path(payload["results"][0]["db_path"]).resolve()
    assert db_path.parent == tmp_path.resolve(), "临时库必须落在指定目录内"
