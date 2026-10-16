"""启动版本检查（2026-10-16 批次十一 · Lead 自办）的突变规格。

五条，各钉一个失效方向。本模块的危险不在崩溃而在**静默说错话**——
它不报错、不影响功能，只是给用户一句错误的行动建议。

* **VC-1** 前端维度把 ``None`` 塌成 ``False`` ⇒ 判不出来时输出
  「前端源码未变，无需重建 dist」⇒ 用户跳过唯一真正需要的动作。
  ⚠️ **这条是 Lead 实际犯过的错**（初版把 deps 的 ``None`` 设成了 ``True``，
  自查抓到的，方向相反但同根），故单独成条。
* **VC-2** 依赖维度把 ``None`` 塌成 ``False`` ⇒ 「依赖清单变化未能判定」
  整句消失。同形不同维度 ⇒ 单列，否则只钉前端那条会漏。
* **VC-3** 过度修正一：把「前端已变」的反向分支也报成要重建 ⇒
  每次纯后端更新都催用户重编前端 ⇒ 提示变成噪声，最终被无视。
* **VC-4** 首次启动也返回 notice ⇒ 每次重启都提示「代码已更新 0000000 → abc1234」。
* **VC-5** 非 git 目录不再降级（去掉 ``if head is None`` 的早退）
  ⇒ zip 安装的用户**服务起不来**。这条守的是「诊断代码不得成为故障源」。

⚠️ 锚点纪律：VC-1 与 VC-2 的锚点都在同一段 ``if/else`` 里，形态相近，
故各自带上独有的注释行做上下文锚定，否则命中两次会 ``[ABORT]``。
"""

MUTATIONS = [
    (
        "VC-1",
        "packages/core/api/version_check.py",
        "    frontend_changed = _paths_changed(repo_root, previous, head, FRONTEND_PATH)\n",
        "    # VC-MUTATION: 未知塌成「没变」\n"
        "    frontend_changed = _paths_changed(repo_root, previous, head, FRONTEND_PATH) is True\n",
        "tests/unit/test_startup_version_check.py",
    ),
    (
        "VC-2",
        "packages/core/api/version_check.py",
        "    if any(r is None for r in dep_results):\n        deps_changed: bool | None = None\n",
        "    if any(r is None for r in dep_results):\n"
        "        deps_changed: bool | None = False  # VC-MUTATION: 未知塌成「没变」\n",
        "tests/unit/test_startup_version_check.py",
    ),
    (
        "VC-3",
        "packages/core/api/version_check.py",
        "        elif self.frontend_changed is False:\n",
        "        elif True:  # VC-MUTATION: 反向分支也催重建 ⇒ 提示变噪声\n",
        "tests/unit/test_startup_version_check.py",
    ),
    (
        "VC-4",
        "packages/core/api/version_check.py",
        "    previous = read_state(data_dir)\n    if previous is None:\n",
        "    previous = read_state(data_dir)  # VC-MUTATION: 首次也当有更新\n"
        "    if False:\n",
        "tests/unit/test_startup_version_check.py",
    ),
    (
        "VC-5",
        "packages/core/api/version_check.py",
        "    head = current_head(repo_root)\n    if head is None:\n        return None\n",
        "    head = current_head(repo_root)  # VC-MUTATION: 不再降级\n"
        "    if False:\n        return None\n",
        "tests/unit/test_startup_version_check.py",
    ),
]
