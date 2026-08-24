"""番茄签约体检——纯函数单元测试。

覆盖：
- 每个检查项至少一正一反用例；
- echo_words 用重复堆叠触发；
- signing_window 三区间断言；
- 少于 1 章返回 info「暂无正文」；
- format_summary 渲染格式。
"""

from __future__ import annotations

from packages.core.signing_check.checks import (
    CONFLICT_WORDS,
    FACE_SLAP_WORDS,
    GOLDEN_FINGER_WORDS,
    HOOK_WORDS,
    CheckItem,
    run_checks,
)
from packages.core.signing_check.service import format_summary, run_signing_check

# ---------------------------------------------------------------------------
# 工厂函数：构造最小可用 chapters payload
# ---------------------------------------------------------------------------


def _ch(number: int, text: str) -> dict:
    return {"number": number, "text": text}


def _protags(*names: str) -> list[str]:
    return list(names)


# ---------------------------------------------------------------------------
# ch1_conflict_300
# ---------------------------------------------------------------------------


def test_ch1_conflict_pass_with_conflict_word():
    chapters = [_ch(1, "李晨被人当面羞辱，全场震惊。后续两千字铺垫剧情。")]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "ch1_conflict_300")
    assert item.level == "pass"


def test_ch1_conflict_pass_with_dialogue():
    # 用「…」引号 + ！ 触发对话句命中
    chapters = [_ch(1, "他猛然站起：「谁敢动我兄弟？！」全场哑口无声后续。")]
    items = run_checks(chapters, _protags("主角"))
    item = next(i for i in items if i.key == "ch1_conflict_300")
    assert item.level == "pass"


def test_ch1_conflict_fail_no_keyword_no_dialogue():
    chapters = [_ch(1, "清晨阳光洒在小镇的青石板上，远处传来一阵鸡鸣声，风和日丽。")]
    items = run_checks(chapters, _protags("主角"))
    item = next(i for i in items if i.key == "ch1_conflict_300")
    assert item.level == "fail"
    assert "300" in item.detail


# ---------------------------------------------------------------------------
# ch1_protagonist_500
# ---------------------------------------------------------------------------


def test_ch1_protagonist_pass():
    chapters = [_ch(1, "李晨一觉醒来，发现自己躺在荒郊野外，风声呼呼作响。")]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "ch1_protagonist_500")
    assert item.level == "pass"


def test_ch1_protagonist_fail():
    # 主角名不在前 500 字
    long_padding = "啊" * 600
    chapters = [_ch(1, long_padding + "李晨登场。")]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "ch1_protagonist_500")
    assert item.level == "fail"


def test_ch1_protagonist_handles_empty_names():
    chapters = [_ch(1, "开篇文字，描述景致与氛围。")]
    items = run_checks(chapters, _protags())  # 空主角名单
    item = next(i for i in items if i.key == "ch1_protagonist_500")
    assert item.level == "fail"


# ---------------------------------------------------------------------------
# ch2_golden_finger
# ---------------------------------------------------------------------------


def test_ch2_golden_finger_pass():
    chapters = [
        _ch(1, "李晨一觉醒来，脑中响起一道声音：「系统已绑定」后续情节。"),
        _ch(2, "他睁开眼，看见面前浮现出一块透明面板，属性条清晰可见。"),
    ]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "ch2_golden_finger")
    assert item.level == "pass"


def test_ch2_golden_finger_fail():
    chapters = [
        _ch(1, "李晨坐在田埂上，看着远处的牛羊发呆，日子平淡如水。"),
        _ch(2, "他进城赶集，买了些日用品便回家继续务农。"),
    ]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "ch2_golden_finger")
    assert item.level == "fail"


# ---------------------------------------------------------------------------
# ch3_climax
# ---------------------------------------------------------------------------


def test_ch3_climax_pass_face_slap():
    chapters = [
        _ch(1, "李晨受尽屈辱，咬牙立誓。"),
        _ch(2, "系统觉醒，他获得新生。"),
        _ch(3, "李晨冷笑一声，一巴掌扇在对方面门上，全场寂静，后悔不已。"),
    ]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "ch3_climax")
    assert item.level == "pass"


def test_ch3_climax_warn_no_keyword():
    chapters = [
        _ch(1, "李晨受尽屈辱，咬牙立誓。"),
        _ch(2, "系统觉醒，他获得新生。"),
        _ch(3, "李晨走在街上，看着春暖花开的风景，心生感慨。"),
    ]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "ch3_climax")
    assert item.level == "warn"


def test_ch3_climax_info_when_missing():
    chapters = [
        _ch(1, "李晨受尽屈辱，咬牙立誓。"),
        _ch(2, "系统觉醒，他获得新生。"),
    ]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "ch3_climax")
    assert item.level == "info"
    assert "第三章" in item.detail


# ---------------------------------------------------------------------------
# chapter_hooks
# ---------------------------------------------------------------------------


def test_chapter_hooks_pass_with_question_mark():
    chapters = [
        _ch(1, "李晨一觉醒来，脑中响起一道声音：「系统已绑定」后续情节……接下来会发生什么？"),
    ]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "chapter_hooks_ch1")
    assert item.level == "pass"


def test_chapter_hooks_warn_no_hook():
    # 末尾 120 字没有任何钩子词/标点
    body = "李晨继续赶路。天气晴朗，远处山脉连绵不断。他走了很远很远的路，风景宜人，心情舒畅，脚步轻快。" * 3
    chapters = [_ch(1, body)]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "chapter_hooks_ch1")
    assert item.level == "warn"


def test_chapter_hooks_pass_with_ellipsis():
    chapters = [_ch(1, "李晨走入山洞，看见石门之上刻着一行字……")]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "chapter_hooks_ch1")
    assert item.level == "pass"


# ---------------------------------------------------------------------------
# chapter_length
# ---------------------------------------------------------------------------


def test_chapter_length_pass_in_range():
    chapters = [_ch(1, "字" * 1800)]
    items = run_checks(chapters, _protags())
    item = next(i for i in items if i.key == "chapter_length_ch1")
    assert item.level == "pass"


def test_chapter_length_warn_too_short():
    chapters = [_ch(1, "字" * 500)]
    items = run_checks(chapters, _protags())
    item = next(i for i in items if i.key == "chapter_length_ch1")
    assert item.level == "warn"
    assert "过短" in item.detail


def test_chapter_length_warn_too_long():
    chapters = [_ch(1, "字" * 3000)]
    items = run_checks(chapters, _protags())
    item = next(i for i in items if i.key == "chapter_length_ch1")
    assert item.level == "warn"
    assert "过长" in item.detail


# ---------------------------------------------------------------------------
# echo_words
# ---------------------------------------------------------------------------


def test_echo_words_pass_normal():
    chapters = [
        _ch(1, "李晨一拳打在对方面门上，对方立刻倒地不起。" * 50),
        _ch(2, "次日，他在林中修炼，感悟天地之气。" * 50),
    ]
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "echo_words")
    assert item.level == "pass"


def test_echo_words_warn_stacked():
    # 「坚定」堆叠：每千字远超 5 次
    chunk = "李晨坚定地迈出步伐，他的眼神坚定，心中意志坚定。"
    chapters = [_ch(1, chunk * 200)]  # ~3000 字，坚定 ~600 次
    items = run_checks(chapters, _protags("李晨"))
    item = next(i for i in items if i.key == "echo_words")
    assert item.level == "warn"
    assert "坚定" in item.detail


def test_echo_words_info_when_empty():
    chapters = [_ch(1, "")]
    items = run_checks(chapters, _protags())
    item = next(i for i in items if i.key == "echo_words")
    assert item.level == "info"


# ---------------------------------------------------------------------------
# signing_window
# ---------------------------------------------------------------------------


def test_signing_window_below_20k():
    chapters = [_ch(1, "字" * 5000)]
    items = run_checks(chapters, _protags())
    item = next(i for i in items if i.key == "signing_window")
    assert item.level == "info"
    assert "2万字" in item.detail


def test_signing_window_between_20k_and_80k():
    chapters = [_ch(1, "字" * 30_000)]
    items = run_checks(chapters, _protags())
    item = next(i for i in items if i.key == "signing_window")
    assert item.level == "info"
    assert "3 次机会" in item.detail


def test_signing_window_above_80k():
    chapters = [_ch(1, "字" * 90_000)]
    items = run_checks(chapters, _protags())
    item = next(i for i in items if i.key == "signing_window")
    assert item.level == "info"
    assert "已过全部签约窗口" in item.detail


# ---------------------------------------------------------------------------
# 边界：少于 1 章 / CheckItem 数据类
# ---------------------------------------------------------------------------


def test_empty_chapters_returns_single_info():
    items = run_checks([], _protags("李晨"))
    assert len(items) == 1
    assert items[0].key == "empty"
    assert items[0].level == "info"


def test_check_item_dataclass_is_frozen():
    item = CheckItem(key="x", level="pass", detail="d", advice="a")
    try:
        item.key = "y"  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        assert "frozen" in str(exc).lower() or "assign" in str(exc).lower()
    else:
        raise AssertionError("CheckItem should be frozen")


# ---------------------------------------------------------------------------
# format_summary
# ---------------------------------------------------------------------------


def test_format_summary_basic_structure():
    result = {
        "project_id": "prj_x",
        "project_name": "测试",
        "total_chars": 1000,
        "generated_at": "2026-01-01T00:00:00",
        "items": [
            {"key": "k1", "level": "pass", "detail": "通过", "advice": "ok"},
            {"key": "k2", "level": "warn", "detail": "警告", "advice": "fix"},
            {"key": "k3", "level": "fail", "detail": "失败", "advice": "must fix"},
            {"key": "k4", "level": "info", "detail": "提示", "advice": "note"},
        ],
        "summary": {"pass_count": 1, "warn_count": 1, "fail_count": 1, "info_count": 1},
    }
    text = format_summary(result)
    assert "===== 签约体检摘要 =====" in text
    assert "通过 1 / 警告 1 / 失败 1 / 信息 1" in text
    assert "[pass] k1：通过" in text
    assert "[warn] k2：警告" in text
    assert "[fail] k3：失败" in text


def test_format_summary_empty_items():
    result = {
        "items": [],
        "summary": {"pass_count": 0, "warn_count": 0, "fail_count": 0, "info_count": 0},
    }
    text = format_summary(result)
    assert "通过 0 / 警告 0 / 失败 0 / 信息 0" in text


# ---------------------------------------------------------------------------
# 词典基本完整性（回归保护：词典被无意改空时立刻失败）
# ---------------------------------------------------------------------------


def test_lexicons_non_empty():
    assert 25 <= len(CONFLICT_WORDS) <= 40
    assert 25 <= len(GOLDEN_FINGER_WORDS) <= 40
    assert 25 <= len(FACE_SLAP_WORDS) <= 40
    assert 15 <= len(HOOK_WORDS) <= 40


# ---------------------------------------------------------------------------
# service.run_signing_check：项目不存在 → ValueError
# ---------------------------------------------------------------------------


def test_service_raises_valueerror_for_unknown_project(tmp_path):
    import pytest

    from packages.core.config import Settings
    from packages.core.db import apply_migrations

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    with pytest.raises(ValueError):
        run_signing_check(settings.db_path, "prj_nonexistent")
