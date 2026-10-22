"""``migrate_backups_to_sync.py`` 的 ``--dry-run``：撤掉「只读计划」⇒ 必红（dev-b 批次二十一 P1-6）。

缺陷形状
--------
该脚本**无 argparse、无 --help、无 --dry-run** ⇒ **跑一次就是一次真实的不可逆迁移**
（openssl 加密 + ``rmtree``/``unlink`` 删源备份）⇒ 操作者**无法在执行前预览**
「会加密什么、会删哪些源、目标落在哪」。

两条突变各钉一个失效方向
------------------------
* **DRYRUN-1** 计划**不记录任何条目** ⇒ ``--dry-run`` 的输出里没有「将加密什么 /
  将删哪些源 / 目标路径」⇒
  ``test_dry_run_prints_the_plan_and_writes_nothing`` 必红（它断言计划输出含
  预期条目）。
* **DRYRUN-2** ``--dry-run`` 分支**不再提前返回** ⇒ 落到真实执行路径 ⇒
  **源被加密后删除** ⇒ 同一用例的「dry-run 不得删除源文件」断言必红。
  ⚠️ 本条突变会**真的**调用 openssl 并删掉夹具里的**合成**源文件（``tmp_path``
  里的假文件）⇒ 真实备份路径**一次都不会被碰**（测试把模块级常量
  MOVES / DEST_ROOT / KEY_FILE 全部指向 ``tmp_path``）。
  ⚠️ **2026-10-23 批次二十六锚点订正**（审计 C F-2）：dry-run 分支新增
  ``--accept-existing`` 提示打印 ⇒ 原 3 行锚点命中 0 次（``[ABORT]`` 形状）。
  锚点已扩到当前分支全文（含提示打印），replacement = 去掉末尾 ``return 0``
  ——语义仍是「不再提前返回」。

判别
----
``tests/unit/test_script_migrate_backups_to_sync.py``（6 例：3 例批次二十的
import 副作用回归 + 3 例本批的 dry-run / help）。
"""

MUTATIONS = [
    (
        "DRYRUN-1",
        "scripts/migrate_backups_to_sync.py",
        '        plan.append({"src": src, "rel": rel, "dest": dest, "action": action})\n',
        "        pass  # MUTATION-DRYRUN: 计划不记录条目\n",
        "tests/unit/test_script_migrate_backups_to_sync.py",
    ),
    (
        "DRYRUN-2",
        "scripts/migrate_backups_to_sync.py",
        "    if args.dry_run:\n"
        "        _print_plan(_plan_moves(MOVES, DEST_ROOT), DEST_ROOT, PRUNE_DIRS)\n"
        "        if not args.accept_existing:\n"
        "            print(\n"
        "                \"[DRY-RUN] 注：实际执行时「目标已存在」默认记**失败 rc=1**\"\n"
        "                \"（除非加 --accept-existing）\"\n"
        "            )\n"
        "        return 0\n",
        "    if args.dry_run:\n"
        "        _print_plan(_plan_moves(MOVES, DEST_ROOT), DEST_ROOT, PRUNE_DIRS)\n"
        "        if not args.accept_existing:\n"
        "            print(\n"
        "                \"[DRY-RUN] 注：实际执行时「目标已存在」默认记**失败 rc=1**\"\n"
        "                \"（除非加 --accept-existing）\"\n"
        "            )\n",
        "tests/unit/test_script_migrate_backups_to_sync.py",
    ),
]
