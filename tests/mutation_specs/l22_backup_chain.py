"""备份链（默认加密 / 密文自检 / 密钥缺失 / 恢复工具）的突变规格。

来源：2026-10-23 批次三十一 task-3（D-2 / D-3 / D-4 / D-5）。每条只撤掉**一处**修复，
定向用例必红。

| id | 撤掉的修复 / 改动 | 必红用例 |
|---|---|---|
| ``BKP-SELFCHECK-1`` | 加密后不做**解密往返自检**（旧实现只对加密前的明文做 integrity_check ⇒ 密文是否可解从未被验证） | ``test_self_check_failure_writes_no_ciphertext_and_deletes_plaintext`` |
| ``BKP-KEY-GEN-1`` | 密钥文件缺失时不再报错（回到**静默生成新密钥** ⇒ 旧备份永久不可解） | ``test_missing_key_file_aborts_instead_of_silently_generating`` |
| ``BKP-ENCRYPT-DEFAULT-1`` | 默认**不**加密（回到 opt-in 的 ``--encrypt``） | ``test_default_backup_is_encrypted_and_decrypts_to_source_content`` |
| ``BKP-RESTORE-WAL-1`` | 恢复前**不删**目标旁的 ``-wal``/``-shm``（旧实现 ⇒ SQLite 回放旧 WAL ⇒ 恢复静默失效且 integrity_check 报绿） | ``test_restore_with_stale_wal_gets_the_right_database``（⚠️ ``test_restore_removes_stale_wal_and_shm`` **不红**——恢复成功后的收尾也会删 sidecar ⇒ 承重的是前一条） |
| ``BKP-RESTORE-ATOMIC-1`` | 恢复改成**直接写**目标（不是 ``os.replace`` 原子替换） | ``test_restore_replaces_target_atomically`` |

⚠️ 锚点均为**多行上下文**（并发批次里单行锚容易随他人改动漂）；replacement 一律
不含双引号（PowerShell argv 剥引号，见 ``q1_style_sample_key.py`` 留痕）。

⚠️ ``BKP-RESTORE-ATOMIC-1`` 的判别口径是**目标文件的 inode / 文件索引变了**
（``os.replace`` 后目标路径指向新文件；直接覆写则保持不变）——本机实测两者可区分。
"""

MUTATIONS = [
    (
        "BKP-SELFCHECK-1",
        "scripts/backup_db.py",
        "                digest = self_check_ciphertext(enc_tmp, plain, key_file)\n",
        '                digest = "0" * 64  # MUTATION-BKP-SELFCHECK-1: 撤掉密文自检\n',
        "tests/unit/test_backup_db.py",
    ),
    (
        "BKP-KEY-GEN-1",
        "scripts/backup_db.py",
        "    if not init:\n        _abort(\n",
        "    if False:  # MUTATION-BKP-KEY-GEN-1: 回到静默生成新密钥\n        _abort(\n",
        "tests/unit/test_backup_db.py",
    ),
    (
        "BKP-ENCRYPT-DEFAULT-1",
        "scripts/backup_db.py",
        "    encrypt = not args.plaintext\n",
        "    encrypt = False  # MUTATION-BKP-ENCRYPT-DEFAULT-1: 回到默认明文\n",
        "tests/unit/test_backup_db.py",
    ),
    (
        "BKP-RESTORE-WAL-1",
        "scripts/restore_db.py",
        "            removed, failed = remove_sidecars(target)\n",
        "            removed, failed = [], []  # MUTATION-BKP-RESTORE-WAL-1: 不删 -wal/-shm\n",
        "tests/unit/test_restore_db.py",
    ),
    (
        "BKP-RESTORE-ATOMIC-1",
        "scripts/restore_db.py",
        "            os.replace(tmp, target)\n",
        "            target.write_bytes(tmp.read_bytes())  # MUTATION: 直接写而非原子替换\n",
        "tests/unit/test_restore_db.py",
    ),
]
