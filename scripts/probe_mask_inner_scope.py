"""只读探针：核实「只撤掉内部那一步 api_key 遮蔽」有没有用例抓得住。

背景：l3 组规格的 docstring 写着「覆盖面不受影响——真有人摘掉 ``_mask_response``
内部的替换，同一条用例会以同样方式变红」。但另一支探针实测表明：摘掉**整个出口**
（``_mask_profile_rows`` 里的 ``_mask_response(...)``）时，两条用例红的原因都是
**端点 500**（``params_json`` 在 DB 里是 JSON 字符串 ⇒ 进不了 ``dict[str,Any]``
校验），**根本没走到「检查明文」那步**。

⇒ 「摘掉出口」与「只摘掉内部那一步 api_key 替换」是**两个不同的场景**，
后者端点不 500、明文真的出参。规格那句「以同样方式变红」**需实测**。

实测结论（2026-10-21）：只撤内部那一步 ⇒ **恰好 1 条**红
（``test_list_rows_never_leak_the_plaintext_api_key``，明文真出参），其余 27 条全绿
⇒ 判别面**无缺口**。

本脚本：备份 → 只撤 ``_mask_params`` 里的 ``out["api_key"] = _MASK`` 一行
（保留 dict 化与 has_key 计算）→ 跑整个配置列表契约测试文件，看**哪些**用例红
→ finally 还原 + 校验 SHA。

⚠️ **本脚本会临时改仓库源码** ⇒ 与 pytest / 突变执行器**禁并发**。
启动时若发现 ``*.mutbak`` 残留即拒绝运行。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

# ⚠️ 本机控制台默认 cp936（AGENTS 坑区「Windows 默认 cp936 吞中文」）：输出流改 utf-8。
# ⚠️ 判 hasattr：pytest 捕获期间 sys.stdout 被换成没有 reconfigure 的对象 ⇒
# 无条件调用会让 **import 就炸**。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "packages/core/model_router/security.py"
BAK = TARGET.with_suffix(".py.probebak")

OLD = '    if has_key:\n        out["api_key"] = _MASK\n'
NEW = '    if False:  # MUTATION: 撤掉 api_key 遮蔽\n        out["api_key"] = _MASK\n'

TEST_FILE = "tests/api/test_config_list_response_contract.py"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _assert_no_mutation_runner() -> None:
    """突变执行器在飞时**拒绝运行**（本脚本与它都会改源码 ⇒ 并发会互相污染）。

    残留 ``.mutbak`` 也可能是「执行器被中断」留下的（坑区「中断执行器也会留下残留」）
    ⇒ 那种情况源码可能仍处于突变态 ⇒ **同样拒绝**（保守是对的）。
    """
    found: list[Path] = []
    for d in ("packages", "tests", "scripts", "database"):
        found += list((ROOT / d).rglob("*.mutbak"))
    if found:
        print(f"[ABORT] 检测到 {len(found)} 个 *.mutbak ⇒ 突变执行器可能在跑或上次被中断")
        for p in found[:5]:
            print(f"        {p.relative_to(ROOT)}")
        raise SystemExit(2)


def main() -> int:
    _assert_no_mutation_runner()
    src = TARGET.read_text(encoding="utf-8")
    if src.count(OLD) != 1:
        print(f"[ABORT] 锚点命中 {src.count(OLD)} 次（要求恰好 1）")
        return 2
    before = sha(TARGET)
    shutil.copy2(TARGET, BAK)
    try:
        TARGET.write_text(src.replace(OLD, NEW), encoding="utf-8")
        print(f"[MUTATED] 已撤掉 _mask_params 里的 api_key 遮蔽（{before[:12]} -> {sha(TARGET)[:12]}）")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", TEST_FILE, "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = proc.stdout
        print(f"\n=== exit={proc.returncode} ===")
        failed = [ln for ln in out.splitlines() if ln.startswith("FAILED")]
        if failed:
            print(f"红了 {len(failed)} 条：")
            for ln in failed:
                print("   " + ln)
        else:
            print("!! 没有任何用例变红 —— 判别面缺失")
        for ln in out.strip().splitlines()[-3:]:
            print("   " + ln)
    finally:
        shutil.copy2(BAK, TARGET)
        BAK.unlink(missing_ok=True)
        after = sha(TARGET)
        ok = after == before
        print(f"\n[RESTORED] 还原校验 {'OK' if ok else 'FAILED'}（{after[:12]} vs {before[:12]}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
