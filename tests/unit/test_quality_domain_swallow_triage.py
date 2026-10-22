"""领域层「吞异常分诊」B 类留痕的判别用例（2026-10-22 批次二十三 · novelos-dev-a）。

覆盖 ``packages/domain/**`` 的 4 处 B 类（批次二十三分诊）：
每条都验证 **「控制流不变（仍不抛、仍按原语义降级）」＋「恰好留下一条可定位的日志」**。

⚠️ **为什么不用 pytest 的 ``caplog``**：仓内
``packages.core.logging_config.configure_logging()`` 会**移除根 logger 的全部
handler** ⇒ caplog 装在根上的 handler 可能被抹掉 ⇒ 记录丢失 ⇒ 断言变成假信号。
⇒ 本文件直接往**具名 logger** 上挂自己的 handler。
"""

from __future__ import annotations

import logging

from packages.domain.chapter import service as chapter_service
from packages.domain.character import service as character_service
from packages.domain.reference import service as reference_service
from packages.domain.volume import service as volume_service
from packages.domain.world import service as world_service


class _Collector(logging.Handler):
    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


class _LogCapture:
    """挂在**具名 logger** 上的日志捕获器（上下管理器）。"""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self.records: list[logging.LogRecord] = []
        self._handler = _Collector(self.records)

    def __enter__(self) -> _LogCapture:
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc: object) -> bool:
        self._logger.removeHandler(self._handler)
        return False

    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]


class _FakeCursor:
    def __init__(self, one: object = None, many: object = ()) -> None:
        self._one = one
        self._many = list(many)

    def fetchone(self) -> object:
        return self._one

    def fetchall(self) -> list:
        return list(self._many)


class _FakeConn:
    """按 SQL 关键字分派的假连接（**不落盘、不碰真库**）。"""

    def __init__(self, handlers: list) -> None:
        self._handlers = handlers

    def execute(self, sql: str, params: object = ()) -> _FakeCursor:
        for predicate, cursor in self._handlers:
            if predicate(sql):
                return cursor
        return _FakeCursor()

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_dirty_plan_json_in_revision_note_is_logged_and_still_degrades(
    monkeypatch,
):
    """B-5：脏 ``chapters.plan_json`` ⇒ 视为空 plan（**不抛**）**且留痕**。

    控制流不变（仍写入 ``revision_note``、不抛），但下游按「该章无 plan」判定时
    与「plan 脏了」不可区分 ⇒ 留痕。
    """
    row = {"status": "DRAFTED", "plan_json": "{not-json"}
    monkeypatch.setattr(
        chapter_service,
        "get_connection",
        lambda *a, **k: _FakeConn(
            [(lambda s: "plan_json" in s, _FakeCursor(one=row))]
        ),
    )
    service = chapter_service.ChapterService("novelos.db")
    with _LogCapture(chapter_service.log) as cap:
        result = service.update_revision_note("ch_1", "这条意见")
    assert result is None or isinstance(result, dict)
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "ch_1" in cap.messages()[0], "日志须带可定位标识（chapter_id）"


def test_dirty_character_aliases_are_logged_and_still_degrade_to_empty():
    """B-6：脏 ``characters.aliases`` ⇒ 别名退化为空列表（**不抛**）**且留痕**。

    本函数无实体 id 入参 ⇒ 日志用**阶段名**（``entities.aliases``）定位。
    """
    with _LogCapture(character_service.log) as cap:
        out = character_service._load_aliases("{not-json")
    assert out == [], "控制流不变：别名退化为空列表"
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "entities.aliases" in cap.messages()[0], "日志须带可定位标识（阶段名）"


def test_dirty_canon_json_is_logged_and_still_degrades_to_empty(monkeypatch):
    """B-7：脏 ``reference_canons.canon_json`` ⇒ 退 ``{}``（**不抛**）**且留痕**。

    同文件 ``summary_of`` 的成文契约是「解析失败 → 保留 row 字段」；这里退成 ``{}``
    ⇒ 摘要信息静默全丢 ⇒ 留痕。
    """
    row = {
        "canon_id": "c1",
        "project_id": "p1",
        "title": "T",
        "reader_profile": None,
        "status": "active",
        "canon_json": "{not-json",
        "report_md": None,
        "created_at": "2026-01-01T00:00:00+08:00",
    }
    handlers = [
        (lambda s: "FROM canon_extracts" in s, _FakeCursor(many=[])),
        (lambda s: "FROM reference_canons" in s, _FakeCursor(one=row)),
    ]
    monkeypatch.setattr(
        reference_service, "get_connection", lambda *a, **k: _FakeConn(handlers)
    )
    service = reference_service.ReferenceService("novelos.db")
    with _LogCapture(reference_service.log) as cap:
        detail = service.get_canon_detail("c1")
    assert detail is not None
    assert detail["canon_json"] == {}, "控制流不变：canon_json 退化为 {}"
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "c1" in cap.messages()[0], "日志须带可定位标识（canon_id）"


def test_dirty_world_aliases_are_logged_and_still_degrade_to_empty():
    """B-8：脏 ``world.aliases`` ⇒ 别名退化为空列表（**不抛**）**且留痕**。

    本函数无实体 id 入参 ⇒ 日志用**阶段名**（``world.aliases``）定位。
    """
    with _LogCapture(world_service.log) as cap:
        out = world_service._load_aliases("{not-json")
    assert out == [], "控制流不变：别名退化为空列表"
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "world.aliases" in cap.messages()[0], "日志须带可定位标识（阶段名）"


def test_dirty_volume_terminal_snapshot_is_logged_and_still_degrades_to_none():
    """B-9：脏 ``volumes.terminal_snapshot_json`` ⇒ 退 ``None``（**不抛**）**且留痕**。

    控制流不变（仍落 ``None``，与「列本身为空 → None」**同形**，符合本仓既有
    约定，见 ``plot/service.py`` 的同款 docstring）⇒ 但**留痕**：否则调用方
    无从分辨「脏 JSON」与「该卷本就没有终局快照」。

    标识：``volume_id`` **取自行本身**——``_row_to_dict`` 是 ``staticmethod``、
    没有 ``volume_id`` 入参 ⇒ **不**改签名（只加日志）。
    """
    row = {"volume_id": "v1", "terminal_snapshot_json": "{not-json"}
    with _LogCapture(volume_service.log) as cap:
        out = volume_service.VolumeService._row_to_dict(row)
    assert out is not None
    assert out["terminal_snapshot_json"] is None, "控制流不变：仍退化为 None"
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "v1" in cap.messages()[0], "日志须带可定位标识（volume_id）"
