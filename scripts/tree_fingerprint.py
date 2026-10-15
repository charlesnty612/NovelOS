#!/usr/bin/env python
"""树指纹：把「全量回归跑的是哪一棵树」变成**可复核的哈希**。

## 为什么需要这个脚本（不是仪式）

AGENTS.md 坑区登记过：「闸门绿了，但结论不成立」——pytest 在**启动时**完成
collect + import，跑一半的编辑不会被已收集的用例读到，于是「全量绿」可能描述的是
一棵**已经被改过的树**。对策是闸门前取树指纹、跑完再取一次、**逐位一致才算数**。

问题在于：那条纪律原本**只以散文形式存在于文档里**，每次靠手写一行临时代码。
后果不是「麻烦」，而是这条纪律的产出**谁都无法复核**——2026-10-15 复算时实测：
同一棵树（523 文件、mtime 最新仍是两天前）算出的哈希与文档里记录的
`890428ca…` 对不上，试了 6 种合理公式（int/float mtime、正反斜杠、排序、
尾换行、分隔符）都复现不出来。**后来者无法区分「树变了」和「当时用了不同
公式」**——而这条纪律存在的全部意义就是捕捉那种细微的真相偏差。
它自己是不可证伪的，等于不是闸门。

固化成本脚本后：任何人跑同一条命令都得到同一串哈希，「不一致」才有唯一解释。

## 用法

```bash
python scripts/tree_fingerprint.py              # 打印 "<文件数> <sha256>"
python scripts/tree_fingerprint.py --roots packages tests   # 自定义范围
python scripts/tree_fingerprint.py --json       # 机器可读
```

闸门流程：

```bash
FP_PRE=$(python scripts/tree_fingerprint.py)
python -m pytest -n 4 -q
FP_POST=$(python scripts/tree_fingerprint.py)
[ "$FP_PRE" = "$FP_POST" ] || echo "树在测试期间变了 ⇒ 本次结论作废"
```

⚠️ **绿灯之后再改动照样作废**：指纹一致只证明「跑的过程中树没变」，不证明
「跑的正是我想跑的那棵树」。改完 `--fix` 之类的东西必须重跑。

## 关于 `test_m1_lock.py` 曾被 `--ignore` 排除（2026-10-16 订正 + 结清）

这个 flag 曾经挂着一个**已被证伪**的理由：「`test_m1_lock.py` 单跑会硬杀整个进程
树，真因未定位」。原记录犯的是推理错误——把「我复现不出来」当成了「真因还没
找到」，于是一条从未成立的现象派生出「规避而非修复」的纪律。**证伪依据**：该
文件单跑实测通过、无任何进程被带走，且**任何跑法下都没出现过**案卷里的
「0 字节输出 / exit 1 / 无 traceback」。详见 `AGENTS.md` 坑区该条。

**✅ 已结清并撤销该 flag**（批次八）：上一版这里写的未决理由是「该文件被长期排除，
其用例是否仍与当前实现同步——单跑是绿的，但未验证它在 xdist 并行全量下的表现」。
该未决项已由一次**不带 `--ignore` 的全量实测**结清：**3312 passed / 1 skipped /
0 failed**（`-n 4`，399.51s，`test_m1_lock.py` 含在内）。本仓确有「单跑绿、全量红」
的先例（坑区「子进程输出编码」条），所以这一条不是靠推理放行的，是靠实测。

顺此查出的两件事已一并修掉：

1. 该文件两条 `cmd_run` 用例**原本都是空壳**——一条 `pytest.skip`、理由指向另一条
   当作「相同路径的覆盖」，而那条的注释里明写「不带 `--force-lock`」。已重写为真跑
   子进程 + 真写活 PID 锁，以**退出码 5 ↔ 2** 构成正反一对。
2. 一个**真缺陷**：`os.kill(pid, 0)` 在 Windows 上**不是稳定的存活探测**
   （`DETACHED_PROCESS` 下抛 `WinError 87`），而 `except OSError: return False`
   把它读成「进程已死」⇒ **M1 单实例锁静默失效**（活实例被判死 ⇒ 第二实例抢锁
   ⇒ 两个实例并发写同一 SQLite，正是 M1 事故根因）。已改用
   `OpenProcess` + `GetExitCodeProcess`，且探测不出结果时**保守判活**。

同一 flag 在 `README.md` 与 `tests/README.md` 两处已同步（批次八）。

## 口径（改动这里 = 历史哈希全部作废，须在 CHANGELOG 登记）

- 根目录：`packages/ tests/ scripts/ database`
- 后缀：`.py` / `.sql`
- 排除：任何名为 `__pycache__` 的目录
- 路径：相对仓库根，正斜杠
- 行格式：`{路径}|{字节长度}|{mtime 整秒}`，按行**字典序**排序
- 拼接：换行符连接，**不加尾换行**
- 摘要：UTF-8 编码后取 SHA256 十六进制

为什么含 mtime：只含内容会漏掉「内容相同但被重写」的中间态；含 mtime 能捕捉
任何一次写入。两段式比对（前/后）只关心**是否变化**，不含 mtime 也够——但单次
存档要能区分「同一棵树」与「被还原过的树」，故保留。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

#: 默认扫描根（相对仓库根）。改这里会让所有历史哈希失效——须在 CHANGELOG 登记。
DEFAULT_ROOTS: tuple[str, ...] = ("packages", "tests", "scripts", "database")
#: 纳入指纹的文件后缀。
SUFFIXES: tuple[str, ...] = (".py", ".sql")
#: 一律跳过的目录名。
EXCLUDED_DIRS: frozenset[str] = frozenset({"__pycache__"})


def repo_root() -> Path:
    """仓库根 = 本脚本的上级目录（scripts/ 的父）。"""
    return Path(__file__).resolve().parent.parent


def iter_source_files(roots: tuple[str, ...], base: Path) -> list[Path]:
    """按排序顺序产出纳入指纹的文件（已转正斜杠相对路径）。"""
    out: list[Path] = []
    for root in roots:
        root_path = (base / root).resolve()
        if not root_path.is_dir():
            print(f"[tree_fingerprint] 跳过不存在的根：{root}", file=sys.stderr)
            continue
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
            for name in filenames:
                if not name.endswith(SUFFIXES):
                    continue
                out.append(Path(dirpath) / name)
    # 统一转成相对仓库根 + 正斜杠后再排序（排序口径必须与哈希口径同源）
    rel = [p.relative_to(base).as_posix() for p in out]
    rel.sort()
    return [base / r for r in rel]


def compute(roots: tuple[str, ...] = DEFAULT_ROOTS, base: Path | None = None) -> dict:
    """计算树指纹。返回 ``{count, sha256}``。"""
    base = base or repo_root()
    files = iter_source_files(roots, base)
    rows: list[str] = []
    for path in files:
        st = path.stat()
        rel = path.relative_to(base).as_posix()
        rows.append(f"{rel}|{st.st_size}|{int(st.st_mtime)}")
    rows.sort()
    digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return {"count": len(rows), "sha256": digest}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="取源码树指纹（闸门前后各一次，逐位一致才算数）"
    )
    ap.add_argument(
        "--roots",
        nargs="+",
        default=list(DEFAULT_ROOTS),
        help=f"扫描根，默认 {' '.join(DEFAULT_ROOTS)}",
    )
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    result = compute(tuple(args.roots))
    if args.json:
        print(json.dumps({"roots": list(args.roots), **result}, ensure_ascii=False))
    else:
        print(f"{result['count']} {result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
