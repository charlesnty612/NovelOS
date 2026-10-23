"""三个高静默风险脚本 + 两个评估脚本落盘默认值的突变补盲（2026-10-23 批次二十九 task-26）。

来源：`docs/reviews/remaining-debt-2026-10-23.md` C-1（切片）/ C-3。审计 C 的排序
`backup_db` > `content_sync` > `open_volume`；三者此前**零突变规格**（l15 只覆盖备份
白名单，不覆盖本脚本的 keep/prune 语义）。C-3 是两个评估脚本默认写仓库 ``docs/``。

五条突变与承接用例（全部已确认用例存在；新增的那条已同批补）
------------------------------------------------------------
| id | 撤掉的修复 / 改动 | 必红用例 |
|---|---|---|
| SC-BACKUP-KEEP | ``prune`` 切片 ``backups[:-keep]`` → ``backups[:-(keep + 1)]``（少删一份，磁盘泄漏） | ``test_script_backup_db_keep.py::test_prune_keeps_exactly_the_newest_n`` |
| SC-CONTENT-WARN | ``genre-pull`` 反向漂移告警不落 stderr → **告警静默丢弃**（stdout JSON 照常） | ``test_content_sync_drift.py::TestPullReverseDriftWarning::test_cli_pull_writes_warning_to_stderr`` |
| SC-OPENVOLUME-GAP | 章号连续性校验短路（``if False:``）→ 跳号 brief 被放行 | ``test_open_volume.py::test_gap_chapter_numbers_rejected`` |
| SC-EVAL-DOCS | ``--write-docs`` 分支失效（落盘回退临时目录） | ``test_script_eval_outputs.py::test_eval_regression_write_docs_flag_writes_repo_docs`` |
| SC-M1-DOCS | 同上（``m1_report --write-docs``） | ``test_script_eval_outputs.py::test_m1_report_write_docs_flag_writes_repo_docs`` |

⚠️ 两条 DOCS 突变刻意打在 ``elif args.write_docs:`` 而不是「把 DEFAULT 改回 docs/」：
后者会让**默认模式测试**真的往仓库 ``docs/`` 写文件（突变期间脏树）。打在 flag 分支上
只让 write-docs 用例红，默认临时路径仍被沿用 ⇒ 实跑不产生仓库写入。

锚点均为多行上下文、全仓唯一（validate_specs / precheck 已验）；replacement 不含
双引号以外的转义形态（PowerShell argv 剥引号，见 ``q1_style_sample_key.py`` 留痕）。
"""

MUTATIONS = [
    (
        "SC-BACKUP-KEEP",
        "scripts/backup_db.py",
        "    for old in backups[:-keep]:\n",
        "    for old in backups[:-(keep + 1)]:  # MUTATION-SC-BACKUP-KEEP: 少删一份\n",
        "tests/unit/test_script_backup_db_keep.py",
    ),
    (
        "SC-CONTENT-WARN",
        "scripts/content_sync.py",
        "    # 告警走 stderr（stdout 只留机器可读 JSON），**不改变 pull 既有行为**。\n"
        "    for w in result.warnings:\n",
        "    # 告警走 stderr（stdout 只留机器可读 JSON），**不改变 pull 既有行为**。\n"
        "    for w in []:  # MUTATION-SC-CONTENT-WARN: 反向漂移告警静默丢弃\n",
        "tests/unit/test_content_sync_drift.py",
    ),
    (
        "SC-OPENVOLUME-GAP",
        "scripts/open_volume.py",
        "    if nums and nums != list(range(nums[0], nums[0] + len(nums))):\n",
        "    if False:  # MUTATION-SC-OPENVOLUME-GAP: 放弃章号连续性校验\n",
        "tests/unit/test_open_volume.py",
    ),
    (
        "SC-EVAL-DOCS",
        "scripts/eval_regression.py",
        "    elif args.write_docs:\n        runs_dir = DOCS_RUNS_DIR\n",
        "    elif False:  # MUTATION-SC-EVAL-DOCS: --write-docs 失效\n"
        "        runs_dir = DOCS_RUNS_DIR\n",
        "tests/unit/test_script_eval_outputs.py",
    ),
    (
        "SC-M1-DOCS",
        "scripts/m1_report.py",
        "    elif args.write_docs:\n        out_path = DOCS_OUT_PATH\n",
        "    elif False:  # MUTATION-SC-M1-DOCS: --write-docs 失效\n"
        "        out_path = DOCS_OUT_PATH\n",
        "tests/unit/test_script_eval_outputs.py",
    ),
]
