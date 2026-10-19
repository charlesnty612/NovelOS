"""题材包版本漂移巡检（内容仓 ``pack.json`` ↔ 运行库 ``genre_packs``）。

**为什么需要它**：题材包是「双轨」资产——运行面在运行库 ``genre_packs`` 表，
编辑面 / 运行面副本在内容仓 ``genres/<slug>/pack.json``。两侧由
``genre-push`` / ``genre-pull`` 单向搬运，但**没有任何机制检测两侧已经分叉**。

实测（2026-10-19 复核，dev 库 + 内容仓）::

    DB   genre_packs['genre-male-quicktrans-v1'].version = 10  (updated_at 2026-09-17)
    文件  genres/male-quicktransmigration/pack.json    .version =  6  (synced_at 2026-09-14)

DB 比文件新 **4 个版本**（DB 侧的编辑结果没回写内容仓），而全链路零告警。
本模块提供 ``detect_genre_drift``：一次**只读**巡检，报出四类状态：

======================  ====================================================
方向（``direction``）  含义
======================  ====================================================
``content_newer``      两边都有、``version`` 不同，**内容仓新**（正常待拉取）
``db_newer``           两边都有、``version`` 不同，**DB 新**（反向漂移）
``content_only``       内容仓有 ``pack.json``、DB 无该 ``pack_id``
``db_only``            DB 有该 ``pack_id``、内容仓无 ``pack.json``
======================  ====================================================

设计要点：

- **只读**：不写运行库、不写内容仓、不改 schema、不调 LLM。
- **判定口径与 ``genre-pull`` / ``genre-push`` 逐字一致**，不另起一套解析：
  内容仓侧走 :meth:`GenrePackDoc.from_dict`（``version`` 缺失 / 非法 → 0，
  ``pack_id`` 缺失 → 回退目录 slug——与 ``pull_genre_pack`` 同款），
  DB 侧走 :class:`GenrePackService` 的行投影（``version`` 列即 DB 版本）。
- **降级范围收窄**（AGENTS 硬规则）：读运行库时的 ``sqlite3.OperationalError``
  **只**在「缺表 / 缺列」（:func:`packages.core.db.is_missing_schema_error`）
  时降级为「DB 侧为空」；``database is locked`` / ``disk I/O error`` 等**故障
  必须上抛**——静默吞掉会让「巡检通过」变成假结论。
- **已知边界**（不可当作更强的保证）：
  ① 只比对 ``version`` 字段，**不比对 payload 内容**（同版本号不同内容查不出）；
  ② DB 的 ``updated_at`` 与内容仓的 ``synced_at`` **语义不同、不可直接比**
  （前者是行更新时间，后者是落盘时间），二者只作人工排查参考，不参与判定。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from packages.core.db import is_missing_schema_error
from packages.core.genre import GenrePackService
from packages.core.logging_config import get_logger

from .paths import GenrePackDoc, genre_pack_path, genres_dir

log = get_logger("novelos.content_sync.drift")

__all__ = [
    "DIRECTION_CONTENT_NEWER",
    "DIRECTION_CONTENT_ONLY",
    "DIRECTION_DB_NEWER",
    "DIRECTION_DB_ONLY",
    "DriftItem",
    "GenreDriftReport",
    "compare_versions",
    "detect_genre_drift",
]


# ---------------------------------------------------------------------------
# 方向常量
# ---------------------------------------------------------------------------

#: 内容仓 version 高于 DB（内容仓新 ⇒ 待 ``genre-pull`` 拉进运行库）。
DIRECTION_CONTENT_NEWER = "content_newer"

#: DB version 高于内容仓（DB 新 ⇒ **反向漂移**：DB 的编辑结果没回写内容仓）。
DIRECTION_DB_NEWER = "db_newer"

#: 内容仓有该题材包、运行库没有（内容仓有 / DB 无）。
DIRECTION_CONTENT_ONLY = "content_only"

#: 运行库有该题材包、内容仓没有（DB 有 / 内容仓无）。
DIRECTION_DB_ONLY = "db_only"


def compare_versions(content_version: int, db_version: int) -> str:
    """比较内容仓与运行库两侧的 ``version``，返回方向常量。

    - 内容仓新 → :data:`DIRECTION_CONTENT_NEWER`
    - DB 新 → :data:`DIRECTION_DB_NEWER`
    - 一致 → **空串**（表示「无漂移」，静默通过）

    单一判定单点：漂移巡检（:func:`detect_genre_drift`）与 ``genre-pull``
    的反向漂移告警都走这里，**不各写一套比较逻辑**（否则两处口径迟早分叉）。
    """
    if content_version > db_version:
        return DIRECTION_CONTENT_NEWER
    if db_version > content_version:
        return DIRECTION_DB_NEWER
    return ""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class DriftItem:
    """一条漂移 / 缺失记录。

    - ``pack_id``：两侧的匹配键（内容仓 ``pack.json`` 的 ``pack_id``，缺失时
      回退目录 slug——与 ``genre-pull`` 同款）。
    - ``direction``：见本模块顶部方向常量表。
    - ``content_version`` / ``db_version``：两侧版本号；某一侧不存在时为 ``None``。
    - ``content_path``：内容仓 ``pack.json`` 路径（DB 侧独有时为 ``None``）。
    - ``db_source_path``：运行库行记录的**内容仓来源路径**（``genre_packs.source_path``，
      形如 ``genres/<slug>``）——「DB 有、内容仓无」时它就是「文件该放在哪」的指针。
    - ``content_synced_at`` / ``db_updated_at``：**仅供人工排查参考，不参与判定**
      （两者语义不同，不可直接比大小）。
    """

    pack_id: str
    direction: str
    content_version: int | None = None
    db_version: int | None = None
    content_path: str | None = None
    slug: str = ""
    content_synced_at: str | None = None
    db_updated_at: str | None = None
    db_source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GenreDriftReport:
    """一次巡检的完整结果。

    - ``drift``：漂移 / 缺失项（见 :class:`DriftItem`）；
    - ``checked``：**两侧 pack_id 并集**的规模（一致项 + 漂移项 + 单边项都计入）；
    - ``errors``：巡检过程中的问题（pack.json 不可解析、``genre_packs`` 表缺失等），
      **不计入 drift**——它们是「巡检自身没能完成比对」的信号。
    """

    drift: list[DriftItem] = field(default_factory=list)
    checked: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """无漂移**且**无巡检错误。"""
        return not self.drift and not self.errors

    def to_dict(self) -> dict[str, Any]:
        """机器可读形态（``--json`` 输出）。"""
        return {
            "drift": [d.to_dict() for d in self.drift],
            "checked": self.checked,
            "ok": self.ok,
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# 两侧取数
# ---------------------------------------------------------------------------


def _read_db_packs(
    db_path: str | Path, report: GenreDriftReport
) -> dict[str, dict[str, Any]]:
    """读运行库 ``genre_packs`` → ``{pack_id: {"version", "updated_at"}}``。

    降级范围收窄（AGENTS 硬规则）：``sqlite3.OperationalError`` **只**在
    「缺表 / 缺列」时降级为「DB 侧为空」并记入 ``report.errors``；
    ``database is locked`` / ``disk I/O error`` 等故障**上抛**。
    """
    try:
        rows = GenrePackService(db_path).list_packs()
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            # 锁 / I/O / 损坏等真实故障：必须上抛——吞掉会让「巡检通过」成为假结论。
            raise
        report.errors.append(f"运行库缺少 genre_packs 表（迁移未跑？）：{exc}")
        log.warning("genre drift: genre_packs 表缺失：%s", exc)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        pack_id = str(row.get("pack_id") or "")
        if not pack_id:
            continue
        out[pack_id] = {
            "version": int(row.get("version") or 0),
            "updated_at": row.get("updated_at"),
            "source_path": row.get("source_path"),
        }
    return out


def _read_content_packs(
    content_dir: str | Path, report: GenreDriftReport
) -> dict[str, dict[str, Any]]:
    """读内容仓 ``genres/<slug>/pack.json`` → ``{pack_id: {...}}``。

    解析口径**逐字复用** ``pull_genre_pack``：``json.loads`` →
    :meth:`GenrePackDoc.from_dict` → ``pack_id`` 缺失时回退目录 slug。
    单个 pack.json 读不动 / 解析失败**不中断整次巡检**（记入 ``report.errors``）。
    """
    out: dict[str, dict[str, Any]] = {}
    gdir = genres_dir(content_dir)
    if not gdir.exists():
        return out
    for child in sorted(gdir.iterdir()):
        if not child.is_dir():
            continue
        ppath = genre_pack_path(child)
        if not ppath.exists():
            continue
        try:
            raw = json.loads(ppath.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report.errors.append(f"内容仓 pack.json 读取失败（{ppath}）：{exc}")
            log.warning("genre drift: pack.json 读取失败 %s：%s", ppath, exc)
            continue
        doc = GenrePackDoc.from_dict(raw)
        # 与 pull_genre_pack 同款回退：pack_id 缺失 → 用目录 slug 当键。
        pack_id = (doc.pack_id or "").strip() or child.name
        if pack_id in out:
            report.errors.append(
                f"内容仓存在重复 pack_id={pack_id}"
                f"（{out[pack_id]['path']} 与 {ppath}）——按排序取首个参与比对"
            )
            continue
        out[pack_id] = {
            "version": doc.version,
            "slug": child.name,
            "path": str(ppath),
            "synced_at": doc.synced_at or None,
        }
    return out


# ---------------------------------------------------------------------------
# 巡检主入口
# ---------------------------------------------------------------------------


def detect_genre_drift(
    db_path: str | Path, content_dir: str | Path
) -> GenreDriftReport:
    """巡检题材包版本漂移（**只读**）。

    对内容仓 ``genres/<slug>/pack.json`` 与运行库 ``genre_packs`` 里同
    ``pack_id`` 的行逐条比对 ``version``，报出四类状态（见本模块顶部表）。

    返回 :class:`GenreDriftReport`；``report.ok`` 为 True 表示「无漂移且无巡检错误」。
    """
    report = GenreDriftReport()
    db_packs = _read_db_packs(db_path, report)
    content_packs = _read_content_packs(content_dir, report)

    all_ids = sorted(set(db_packs) | set(content_packs))
    report.checked = len(all_ids)

    for pack_id in all_ids:
        content = content_packs.get(pack_id)
        db = db_packs.get(pack_id)
        if content is not None and db is not None:
            direction = compare_versions(content["version"], db["version"])
            if not direction:
                continue  # 一致 → 静默通过
            report.drift.append(
                DriftItem(
                    pack_id=pack_id,
                    direction=direction,
                    content_version=content["version"],
                    db_version=db["version"],
                    content_path=content["path"],
                    slug=content["slug"],
                    content_synced_at=content["synced_at"],
                    db_updated_at=db["updated_at"],
                    db_source_path=db["source_path"],
                )
            )
        elif content is not None:
            report.drift.append(
                DriftItem(
                    pack_id=pack_id,
                    direction=DIRECTION_CONTENT_ONLY,
                    content_version=content["version"],
                    db_version=None,
                    content_path=content["path"],
                    slug=content["slug"],
                    content_synced_at=content["synced_at"],
                )
            )
        elif db is not None:
            report.drift.append(
                DriftItem(
                    pack_id=pack_id,
                    direction=DIRECTION_DB_ONLY,
                    content_version=None,
                    db_version=db["version"],
                    content_path=None,
                    slug="",
                    db_updated_at=db["updated_at"],
                    db_source_path=db["source_path"],
                )
            )

    log.info(
        "genre drift: checked=%d drift=%d errors=%d",
        report.checked,
        len(report.drift),
        len(report.errors),
    )
    return report
