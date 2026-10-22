"""证据表看守自身的三条 minor 修复（2026-10-23 批次二十七 task-22）。

来源：批次二十六双轴审查 M-1 / M-2 / M-3（0 critical / 0 major）。三条都不是业务逻辑，
而是**看守自己的失效方向**——修前实测留证（``tmp/evidence_m1m2_pre_fix.log``）：

* M-1 ``check_table(table, descriptions=lambda: {})`` ⇒ **errors=0 / drifts=0 /
  consistent=True** —— Q-4「登记 ↔ 活 description」半边整体静默失效；
* M-2 resolver 对 ``kind="regex"`` 返回 int ⇒ **抛 AttributeError**（CLI 收口成 exit 2，
  但库调用方会崩）；
* M-3 ``HUMAN_BASELINE.md`` §8 自称「副本、无机器看守」——改 JSON 或改文档都无人红。

四条突变（← 撤回修复即回到修前行为）
------------------------------------
| id | 撤销的修复 | 必红用例 |
|---|---|---|
| EV-M1-DESC    | 撤「登记了 description_numbers 但取不到 live description ⇒ error」 | ``test_guard_is_red_when_descriptions_injection_is_empty`` |
| EV-M2-REGEX   | 撤 ``kind="regex"`` 的类型预校验（回到直接 ``_fingerprint_regex``） | ``test_regex_kind_with_non_pattern_reports_drift_not_crash`` |
| EV-M3-VALUE   | 把 §8 表格里 ``dev_db_92ch`` 的 ``in_version_control`` 改成 ``true`` | ``test_human_baseline_section8_matches_evidence_corpora`` |
| EV-M3-ROW     | 删掉 §8 表格里 ``dev_db_92ch`` 整行 | 同上（corpus_id 集合不等） |

锚点唯一性 / 身份已验证（validate_specs / precheck 0）；replacement 不含双引号以外的
转义形态（PowerShell argv 剥引号，见 ``q1_style_sample_key.py`` 留痕）。
"""

MUTATIONS = [
    (
        "EV-M1-DESC",
        "scripts/detector_evidence_check.py",
        "            registrations = entry.get(\"description_numbers\")\n"
        "            if isinstance(registrations, list) and registrations:\n"
        "                result.errors.append(\n",
        "            registrations = entry.get(\"description_numbers\")\n"
        "            if False:  # MUTATION-EV-M1: 撤掉 M-1 的显式报错（静默 continue）\n"
        "                result.errors.append(\n",
        "tests/unit/quality/test_detector_evidence.py",
    ),
    (
        "EV-M2-REGEX",
        "scripts/detector_evidence_check.py",
        "        if not isinstance(actual, re.Pattern):\n"
        "            result.drifts.append(\n",
        "        if False:  # MUTATION-EV-M2: 撤 regex 类型守卫（非 Pattern 直接崩）\n"
        "            result.drifts.append(\n",
        "tests/unit/quality/test_detector_evidence.py",
    ),
    (
        "EV-M3-VALUE",
        "tests/unit/quality/HUMAN_BASELINE.md",
        "| `dev_db_92ch` | generated | 92 | 本机 dev 库 `data/novelos.db` | `false` | `本机` |\n",
        "| `dev_db_92ch` | generated | 92 | 本机 dev 库 `data/novelos.db` | `true` | `本机` |\n",
        "tests/unit/quality/test_detector_evidence.py",
    ),
    (
        "EV-M3-ROW",
        "tests/unit/quality/HUMAN_BASELINE.md",
        "| `dev_db_92ch` | generated | 92 | 本机 dev 库 `data/novelos.db` | `false` | `本机` |\n",
        "",
        "tests/unit/quality/test_detector_evidence.py",
    ),
]
