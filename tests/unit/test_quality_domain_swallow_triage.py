"""领域层「吞异常分诊」B 类留痕的判别用例（2026-10-22 批次二十三 · novelos-dev-a）。

覆盖 ``packages/domain/**`` 的 4 处 B 类（批次二十三分诊）：
每条都验证 **「控制流不变（仍不抛、仍按原语义降级）」＋「恰好留下一条可定位的日志」**。

⚠️ **为什么不用 pytest 的 ``caplog``**：仓内
``packages.core.logging_config.configure_logging()`` 会**移除根 logger 的全部
handler** ⇒ caplog 装在根上的 handler 可能被抹掉 ⇒ 记录丢失 ⇒ 断言变成假信号。
⇒ 本文件直接往**具名 logger** 上挂自己的 handler。

⚠️ **而且必须显式设 logger 级别**（2026-10-23 task-12 X-1 修复）：具名 logger 没有
自己的级别时**有效级别继承根 logger**，而 ``configure_logging`` 设了根级别**从不
复原** ⇒ 任何一次 ``create_app(log_level="ERROR")`` 都会让本文件的断言恒红。
详见 :class:`_LogCapture` 的 docstring。
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
    """挂在**具名 logger** 上的日志捕获器（上下管理器）。

    ⚠️ **必须显式设 logger 级别**（2026-10-23 task-12 X-1 修复）：

    具名 logger 若**没有自己的级别**（``level == NOTSET``），其**有效级别**由
    **根 logger 继承** ⇒ 而仓内 ``packages.core.logging_config.configure_logging()``
    会 ``root.setLevel(level)`` 且**从不复原** ⇒ 任何一次
    ``create_app(Settings(log_level="ERROR"))``（仓内 6 处，见
    ``tests/api/test_openapi_contract.py:86`` 等）都会把**根级别**永久压成 ERROR
    ⇒ WARNING 记录在**到达 handler 之前**就被 logger 级别滤掉 ⇒ 本捕获器看到
    **0 条** ⇒ ``assert len(cap.records) == 1`` 恒红。

    ⇒ 本类在 ``__enter__`` 显式 ``setLevel(DEBUG)``（把级别钉在自己身上，不再
    继承根级别），``__exit__`` **复原原级别**（不留下副作用给后续用例）。

    ⚠️ 这与「不改生产代码」是配套的：根因在 ``configure_logging`` 不复原根级别
    （生产代码，本任务只读）⇒ 治标在**测试侧**把级别钉死。
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self.records: list[logging.LogRecord] = []
        self._handler = _Collector(self.records)
        self._prev_level: int = logger.level

    def __enter__(self) -> _LogCapture:
        self._prev_level = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc: object) -> bool:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prev_level)
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
