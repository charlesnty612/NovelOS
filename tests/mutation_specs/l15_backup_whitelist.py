"""审计 C 修复小批的突变规格（2026-10-23 批次二十六）。

F-1 备份白名单漏表 / F-2 迁移假成功 / F-3 dry-run 写副作用 / F-4 退出码契约 /
F-6 ``--keep`` 语义。每条只撤掉**一处**修复，定向用例必红。

| id | 撤销的修复 | 必红用例 |
|---|---|---|
| ``BKP-WL-1`` | ``EXPORTED_TABLES`` 删 ``chapter_scene_plans`` | ``test_project_scoped_migration_tables_are_exported_or_exempt`` + ``test_roundtrip_preserves_scene_plans_and_skill_bindings`` |
| ``BKP-WL-2`` | ``EXPORTED_TABLES`` 删 ``project_skills`` | 同上 |
| ``BKP-SKIP-WARN-1`` | ``project_skills`` 缺失 skill 时不留可定位 warning | ``test_import_skips_binding_with_locatable_warning_when_skill_missing`` |
| ``MIGRATE-SKIP-1`` | 目标已存在不再记失败（回到静默 rc 0） | ``test_existing_target_with_source_present_is_failure`` |
| ``MIGRATE-SMOKE-1`` | 撤掉加密后的解密冒烟 | ``test_success_path_encrypts_then_decrypt_smoke_then_deletes_source`` |
| ``NORMALIZE-RO-1`` | dry-run 连接退回 RW（``mode=ro`` 撤掉） | ``test_readonly_connection_rejects_writes`` |
| ``NORMALIZE-EXIT-2`` | 顶层 except 收窄到「永不命中的类型」 | ``test_not_a_database_returns_2_single_line`` |
| ``DBM-EXIT-1`` | ``db_maintenance`` 顶层 except 收窄 | ``test_internal_error_returns_exit_2_with_single_line_diagnostic`` |
| ``BACKUP-KEEP-1`` | 撤掉 ``--keep >= 1`` 守卫 | ``test_keep_zero_is_rejected_by_argparse`` |

⚠️ 锚点均为**多行上下文**（并发批次里单行锚容易随他人改动漂）；replacement 一律
不含双引号（PowerShell argv 剥引号，见 ``q1_style_sample_key.py`` 留痕）。
"""

MUTATIONS = [
    (
        "BKP-WL-1",
        "packages/core/backup/schema.py",
        '    "chapter_scene_plans",\n',
        "",
        "tests/unit/test_backup_export_import.py",
    ),
    (
        "BKP-WL-2",
        "packages/core/backup/schema.py",
        '    "project_skills",\n',
        "",
        "tests/unit/test_backup_export_import.py",
    ),
    (
        "BKP-SKIP-WARN-1",
        "packages/core/backup/service.py",
        "                self._import_warnings.append(\n"
        '                    "project_skills: 项目 "\n'
        '                    f"{old_project_id!r} 的绑定 skill {skill_id!r} 在目标库不存在"\n'
        '                    "（跨作品资产不随项目包导出），该绑定已跳过"\n'
        "                )\n",
        "                pass  # MUTATION-BKP-SKIP-WARN-1: 撤掉可定位 warning\n",
        "tests/unit/test_backup_export_import.py",
    ),
    (
        "MIGRATE-SKIP-1",
        "scripts/migrate_backups_to_sync.py",
        "                    \"重跑，或人工处理后删除目标\"\n"
        "                )\n"
        "                ok = False\n",
        "                    \"重跑，或人工处理后删除目标\"\n"
        "                )\n"
        "                pass  # MUTATION-MIGRATE-SKIP-1: 目标已存在不记失败\n",
        "tests/unit/test_script_backup_migrate_safety.py",
    ),
    (
        "MIGRATE-SMOKE-1",
        "scripts/migrate_backups_to_sync.py",
        "            decrypt_smoke(dest)\n",
        "            pass  # MUTATION-MIGRATE-SMOKE-1: 撤解密冒烟\n",
        "tests/unit/test_script_backup_migrate_safety.py",
    ),
    (
        "NORMALIZE-RO-1",
        "scripts/normalize_chapters.py",
        '        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)\n',
        "        conn = sqlite3.connect(str(db_path))  # MUTATION-NORMALIZE-RO-1: 撤只读\n",
        "tests/unit/test_script_backup_normalize_safety.py",
    ),
    (
        "NORMALIZE-EXIT-2",
        "scripts/normalize_chapters.py",
        "    except sqlite3.Error as exc:\n"
        "        # 顶层兜底（F-3）：数据库错误 → 单行诊断 + exit 2（不再裸 traceback + exit 1）。\n",
        "    except ZeroDivisionError as exc:  # MUTATION-NORMALIZE-EXIT-2: 收窄到永不命中\n",
        "tests/unit/test_script_backup_normalize_safety.py",
    ),
    (
        "DBM-EXIT-1",
        "scripts/db_maintenance.py",
        "    except Exception as exc:  # noqa: BLE001 —— CLI 顶层兜底（契约承诺 exit 2）\n",
        "    except ZeroDivisionError as exc:  # MUTATION-DBM-EXIT-1: 收窄到永不命中\n",
        "tests/unit/test_script_backup_dbmaintenance_exit.py",
    ),
    (
        "BACKUP-KEEP-1",
        "scripts/backup_db.py",
        "    if args.keep < 1:\n"
        '        ap.error("--keep 必须 >= 1（保留最近 N 份；0 的语义有歧义，已拒绝）")\n',
        "    if False:  # MUTATION-BACKUP-KEEP-1: 撤 keep 守卫\n"
        '        ap.error("--keep 必须 >= 1（保留最近 N 份；0 的语义有歧义，已拒绝）")\n',
        "tests/unit/test_script_backup_db_keep.py",
    ),
]
