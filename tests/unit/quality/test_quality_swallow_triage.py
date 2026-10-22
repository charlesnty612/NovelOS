"""质量域「吞异常分诊」B 类留痕的判别用例（2026-10-22 批次二十三 · novelos-dev-a）。

覆盖 ``packages/core/quality/service.py`` 的 4 处 B 类（批次二十三分诊）：
每条都验证 **「控制流不变（仍不抛、仍按原语义降级）」＋「恰好留下一条可定位的日志」**。

⚠️ **为什么不用 pytest 的 ``caplog``**
------------------------------------
仓内 ``packages.core.logging_config.configure_logging()`` 会
**移除根 logger 上的全部 handler**（``for h in list(root.handlers): root.removeHandler(h)``）
⇒ caplog 装在根上的 handler 可能被抹掉 ⇒ 记录丢失 ⇒ 断言变成「恒红/恒绿」的
假信号。⇒ 本文件直接往**具名 logger** 上挂自己的 handler，绕开根 handler 的手术。

⚠️ **而且必须显式设 logger 级别**（2026-10-23 task-12 X-1 修复）：具名 logger 没有
自己的级别时**有效级别继承根 logger**，而 ``configure_logging`` 设了根级别**从不
复原** ⇒ 任何一次 ``create_app(log_level="ERROR")`` 都会让本文件的断言恒红。
详见 :class:`_LogCapture` 的 docstring。
"""

from __future__ import annotations

import logging
from pathlib import Path

from packages.core.quality import service as quality_service


class _Collector(logging.Handler):
    """把日志记录收集到列表。"""

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
    ``create_app(Settings(log_level="ERROR"))``（仓内 6 处）都会把**根级别**永久
    压成 ERROR ⇒ WARNING 记录在**到达 handler 之前**就被 logger 级别滤掉 ⇒ 本
    捕获器看到 **0 条** ⇒ ``assert len(cap.records) == 1`` 恒红。

    ⇒ 本类在 ``__enter__`` 显式 ``setLevel(DEBUG)``（把级别钉在自己身上，不再
    继承根级别），``__exit__`` **复原原级别**（不留下副作用给后续用例）。
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


def test_dirty_plan_json_is_logged_and_still_degrades_to_empty_plan(monkeypatch):
    """B-1：脏 ``chapters.plan_json`` ⇒ 退空 plan（**不抛**）**且留痕**。

    判别点：撤掉那条 ``log.warning`` ⇒ 本用例**恰好 1 条**变红。
    """
    row = {"plan_json": "{not-json"}
    monkeypatch.setattr(
        quality_service,
        "get_connection",
        lambda *a, **k: _FakeConn(
            [(lambda s: "plan_json" in s, _FakeCursor(one=row))]
        ),
    )
    with _LogCapture(quality_service.log) as cap:
        result = quality_service._read_chapter_plan("novelos.db", "ch_1")
    assert result == {}, "控制流不变：仍退化为空 plan"
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "ch_1" in cap.messages()[0], "日志须带可定位标识（chapter_id）"


def test_unreadable_reference_file_is_logged_and_still_skipped(tmp_path: Path):
    """B-2：参照文件读失败 ⇒ 跳过该文件（**不抛**）**且留痕**。

    这条对应 ``load_reference_texts`` 的 docstring 明写的
    「文件读取异常 → 跳过该文件（**log warning** 但不阻断）」——原先只有 ``continue``、
    **承诺的 warning 从未兑现**。
    """
    refs = tmp_path / "references" / "proj1"
    refs.mkdir(parents=True)
    (refs / "broken.txt").mkdir()  # 目录冒充 .txt ⇒ read_text 抛 OSError
    with _LogCapture(quality_service.log) as cap:
        out = quality_service.load_reference_texts(tmp_path / "novelos.db", "proj1")
    assert out == [], "控制流不变：跳过该文件"
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "broken.txt" in cap.messages()[0], "日志须带可定位标识（文件路径）"


def test_unreadable_reference_file_in_stats_is_logged_and_still_counted_zero(
    tmp_path: Path,
):
    """B-3：参照文件读失败 ⇒ 该文件按 **0 字**计入统计（**不抛**）**且留痕**。

    文件**存在**却报 0 字 = 统计失真，而调用方（quality_gate / evaluate）无从分辨。
    """
    refs = tmp_path / "references" / "proj1"
    refs.mkdir(parents=True)
    (refs / "broken.txt").mkdir()
    with _LogCapture(quality_service.log) as cap:
        out = quality_service.capture_reference_consumption(
            tmp_path / "novelos.db", "proj1"
        )
    assert out["total_chars"] == 0, "控制流不变：该文件按 0 字计"
    assert out["files_count"] == 1
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "broken.txt" in cap.messages()[0], "日志须带可定位标识（文件路径）"


def test_dirty_state_delta_payload_is_logged_and_still_counts_zero_payoff(
    monkeypatch,
):
    """B-4：脏 ``state_deltas.payload_json`` ⇒ 该章 payoff 记 **0**（**不抛**）**且留痕**。

    记 0 与「确实没有 payoff」**同形** ⇒ 脏数据与「无 payoff」不可区分 ⇒ 留痕。
    """
    handlers = [
        (
            lambda s: "FROM chapters" in s,
            _FakeCursor(many=[{"chapter_id": "ch_1", "number": 1}]),
        ),
        (lambda s: "FROM commits" in s, _FakeCursor(one={"delta_id": "d1"})),
        (
            lambda s: "FROM state_deltas" in s,
            _FakeCursor(one={"payload_json": "{not-json"}),
        ),
    ]
    monkeypatch.setattr(
        quality_service, "get_connection", lambda *a, **k: _FakeConn(handlers)
    )
    with _LogCapture(quality_service.log) as cap:
        history = quality_service.compute_payoff_history("novelos.db", "p1")
    assert history == [0], "控制流不变：该章 payoff 记 0"
    assert len(cap.records) == 1, f"应恰好留痕 1 条，实际 {cap.messages()}"
    assert "ch_1" in cap.messages()[0], "日志须带可定位标识（chapter_id）"
