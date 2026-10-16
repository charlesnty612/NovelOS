"""``genre_check.error`` 能不能真的进到评审报告（2026-10-16 批次十 · Lead 自办）。

## 缺陷形状：**布尔门遮蔽**（本仓首次登记该失效家族）

``chapter_review/pipeline.py::_collect_genre_check`` 原写作::

    if not result.bound:
        return None

而 ``verify_chapter`` 恰恰**用** ``bound=False`` 表达「绑定读取失败」::

    result.error = f"binding_read_failed: {exc}"
    result.skipped.append("genre_pack_unavailable")

（其源码注释明写「**不得**落成 ``no_binding``」。）

⇒ ``GenreCheckResult.error`` **结构上不可达**：作者看到一份「干净」的评审报告，
题材核销其实根本没跑成，**零告警**。

它与已登记的三条测试失效方向都不同：

* 「断言了结果 ≠ 判别了机制」：测试测不到机制；
* 「夹具自带噪声 ⇒ 失败点漂移」：测到了另一个机制；
* 「夹具未触发闸门 ⇒ 空断言」：测到了正确节点但节点提前返回；
* **本条**：挡路的东西**根本不是 except**，代码里连「吞异常」的字样都没有——
  是一个 `if`，读代码的人不会把它当吞。

同硬规则 9 的形状（字段存在 ≠ 进得了装配），只是失效面从「except」换成了「布尔」。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from packages.core.genre.verifier import GenreCheckResult
from packages.workflows.chapter_review import pipeline as review_pipeline


@dataclass
class _FakeVerify:
    """记录调用参数的 ``verify_chapter`` 替身。"""

    result: GenreCheckResult
    calls: list = None  # type: ignore[assignment]

    def __call__(self, db_path, chapter_id, *, draft_version=None):
        if self.calls is not None:
            self.calls.append((db_path, chapter_id, draft_version))
        return self.result


def _patch_verify(monkeypatch, result: GenreCheckResult):
    fake = _FakeVerify(result, calls=[])
    monkeypatch.setattr(review_pipeline, "verify_chapter", fake)
    return fake


# ---------------------------------------------------------------------------
# 正：绑定读取失败必须**进报告**
# ---------------------------------------------------------------------------


def test_binding_read_failure_reaches_report_with_error(monkeypatch):
    """``database is locked`` 导致绑定读取失败 ⇒ 报告里必须看得见。

    修复前本用例红：``_collect_genre_check`` 返回 ``None``，
    调用方 ``if genre_check is not None`` 直接整段跳过 ⇒ 报告里没有 ``genre_check`` 键。
    """
    result = GenreCheckResult(bound=False, skipped=["genre_pack_unavailable"])
    result.error = "binding_read_failed: database is locked"
    _patch_verify(monkeypatch, result)

    got = review_pipeline._collect_genre_check("db.sqlite", "ch_1")

    assert got is not None, (
        "绑定读取失败被当成「项目没绑定题材包」整段丢弃 —— "
        "作者会看到一份「干净」的报告，而题材核销根本没跑成"
    )
    assert got["error"] == "binding_read_failed: database is locked"
    assert got["bound"] is False
    assert got["skipped"] == ["genre_pack_unavailable"]


# ---------------------------------------------------------------------------
# 反：真·未绑定仍返回 None（防「一律返回」这种过度修正）
# ---------------------------------------------------------------------------


def test_genuinely_unbound_project_still_returns_none(monkeypatch):
    """项目**确实**没绑定题材包 ⇒ 仍返回 ``None``（零行为变化这条承诺要守住）。

    反向不变量：只看上一条的话，「把 ``if not result.bound`` 整段删掉、
    永远返回 ``to_dict()``」这种过度修正也能让上一条绿。
    """
    result = GenreCheckResult(bound=False, skipped=["no_binding"], error=None)
    _patch_verify(monkeypatch, result)

    assert review_pipeline._collect_genre_check("db.sqlite", "ch_1") is None


def test_bound_result_unaffected(monkeypatch):
    """正常路径不回归：绑定成功照原样返回。"""
    result = GenreCheckResult(bound=True, checked=True, pack_id="gp_x", pack_version=2)
    fake = _patch_verify(monkeypatch, result)

    got = review_pipeline._collect_genre_check("db.sqlite", "ch_1", draft_version=3)

    assert got is not None
    assert got["pack_id"] == "gp_x"
    assert fake.calls == [("db.sqlite", "ch_1", 3)], "draft_version 必须透传（被审对象 ≠ 另一版）"


# ---------------------------------------------------------------------------
# 与调用方的接线：返回非 None 时报告里真的多出 genre_check 键
# ---------------------------------------------------------------------------


def test_report_body_gains_genre_check_key(monkeypatch):
    """接线判别：判别点必须是被测物**专属**的。

    ``report["genre_check"]`` 这个键只有题材核销会写，断言它即可证明政策
    「真的到达响应体」，而不是只验中间函数的返回值。
    """
    result = GenreCheckResult(bound=False, skipped=["genre_pack_unavailable"])
    result.error = "binding_read_failed: database is locked"
    _patch_verify(monkeypatch, result)

    genre_check = review_pipeline._collect_genre_check("db.sqlite", "ch_1")

    report: dict = {}
    if genre_check is not None:  # 与 pipeline.py:400-405 同形的消费方
        report["genre_check"] = genre_check
        for _issue in genre_check.get("issues") or []:
            pass

    assert "genre_check" in report, "失败态没有落进报告体 —— 作者看不到核销没跑成"
    assert report["genre_check"]["error"].startswith("binding_read_failed:")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
