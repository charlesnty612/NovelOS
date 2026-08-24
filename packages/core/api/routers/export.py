"""Export REST 路由（V1.4，Sprint 16）。

端点（挂在 ``/api`` 前缀下）：

- ``GET /projects/{project_id}/export?format={txt|docx|fanqie}[&chapter_no=...]``
  —— 整书/单章/番茄投稿包导出，触发浏览器下载。

错误码映射：
- 404 — project 不存在；
- 400 — 非法 ``format`` 或单章导出缺 `chapter_no` 或 `chapter_no` 不存在；
- 500 — 异常。

设计要点：
- 复用 ``packages/core/exporter`` 的纯函数（``build_txt / build_docx /
  build_fanqie_package``），router 只负责参数解析与 HTTP 头；
- 文件名：含项目名（ASCII 兜底 + ``filename*=UTF-8''…``），参考
  ``packages/core/api/routers/quality.py`` 的 ``q8-export`` 端点；
- ``discover_routers`` 自动发现。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from packages.core.exporter import ExportScope, build_docx, build_fanqie_package, build_txt
from packages.core.logging_config import get_logger
from packages.domain.project.service import ProjectService

log = get_logger("novelos.routers.export")

router = APIRouter(tags=["export"])


def _project(request: Request) -> dict:
    settings = request.app.state.settings
    proj = ProjectService(settings.db_path).get(_project_id(request))
    if proj is None:
        raise HTTPException(status_code=404, detail="project not found")
    return proj


def _project_id(request: Request) -> str:
    return request.path_params["project_id"]


def _safe_ascii_fallback(name: str) -> str:
    """把非 ASCII 项目名转成 ASCII 兜底（去除非字母数字字符，限制长度）。

    全中文场景下，剥离后可能只剩空字符串；这种情形强制用 ``"project"`` 兜底，避免
    ``Content-Disposition`` 出现 ``filename=""`` 的非法值。
    """
    cleaned = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "_" for ch in name)
    cleaned = cleaned.strip("_") or "project"
    return cleaned[:64]


def _content_disposition(filename_ascii: str, filename_utf8: str) -> str:
    """RFC 5987 双写法：ASCII 兜底 + filename* UTF-8。"""
    return (
        f'attachment; filename="{filename_ascii}"; '
        f"filename*=UTF-8''{filename_utf8}"
    )


def _utf8_urlencoded(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")


@router.get("/projects/{project_id}/export")
def export_project(
    project_id: str,
    request: Request,
    format: str = "txt",  # noqa: A002 - FastAPI query param name 与 endpoint 对齐
    chapter_no: int | None = None,
) -> Response:
    """导出整书 / 单章 / 番茄投稿包。

    - ``format=txt``：整书或单章 txt（带 UTF-8 BOM）；
    - ``format=docx``：整书或单章 docx（最小 OOXML）；
    - ``format=fanqie``：番茄投稿包 txt（正文 + 大纲），``chapter_no`` 被忽略。
    - ``chapter_no`` 仅对 ``txt / docx`` 有效；缺参 =整书。
    """
    fmt = (format or "").strip().lower()
    if fmt not in ("txt", "docx", "fanqie"):
        raise HTTPException(
            status_code=400,
            detail=f"unsupported format {format!r} (allowed: txt, docx, fanqie)",
        )

    proj = _project(request)  # 404 if not exists
    project_name = proj.get("name") or "project"

    if fmt == "fanqie":
        try:
            body = build_fanqie_package(str(request.app.state.settings.db_path), project_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("fanqie export failed: %s", exc)
            raise HTTPException(status_code=500, detail="export failed") from exc
        stem = _safe_ascii_fallback(f"{project_name}-fanqie")
        filename_utf8 = f"{project_name}-番茄投稿包.txt"
        return Response(
            content=body,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": _content_disposition(
                    f"{stem}.txt", _utf8_urlencoded(filename_utf8)
                ),
            },
        )

    # txt / docx：可指定 chapter_no
    if chapter_no is not None and chapter_no <= 0:
        raise HTTPException(status_code=400, detail="chapter_no must be >= 1")
    scope = ExportScope(
        kind="chapter" if chapter_no is not None else "book",
        chapter_no=chapter_no,
    )

    try:
        if fmt == "txt":
            body = build_txt(str(request.app.state.settings.db_path), project_id, scope)
            media = "text/plain; charset=utf-8"
            ext = "txt"
        else:  # docx
            body = build_docx(str(request.app.state.settings.db_path), project_id, scope)
            # docx MIME type per OOXML spec
            media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ext = "docx"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("export failed: %s", exc)
        raise HTTPException(status_code=500, detail="export failed") from exc

    suffix = f"-ch{chapter_no}" if chapter_no is not None else "-book"
    stem = _safe_ascii_fallback(f"{project_name}{suffix}")
    filename_utf8 = f"{project_name}{suffix}.{ext}"
    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": _content_disposition(
                f"{stem}.{ext}", _utf8_urlencoded(filename_utf8)
            ),
        },
    )


__all__ = ["router"]
