"""Reference Canon REST 路由（Sprint 11 上半 + V1.5 架构整理）。

挂在 ``/api`` 前缀下（discover_routers 自动发现）。

端点：
- ``POST /projects/{project_id}/deconstruct``  —— 启动 deconstruct-book 工作流；201
- ``GET  /projects/{project_id}/canons``       —— 列出项目下全部 active canon；按 created_at DESC
- ``GET  /canons/{canon_id}``                  —— 单一 canon 全文（canon_json + report_md）
- ``DELETE /canons/{canon_id}``                —— 级联删除 canon + 关联 extracts；204
- ``POST /projects/{project_id}/canons/{canon_id}/to-style-sample``
  —— 把拆书 canon 合成文风样例卡并落入 author_style_samples；201

错误码映射：
- 404 — project / canon 不存在；
- 422 — 请求体字段非法或 workflow 注册缺失；
- 500 — workflow run 中未捕获异常。

设计要点：
- V1.5 起，路由层不再直接写 SQL——所有引用数据访问走
  :class:`packages.domain.reference.ReferenceService`；路由仅做参数校验 + 调用 +
  错误映射。
- POST deconstruct：复用 :class:`WorkflowEngine.start_with_nodes` 模式（与 workflows.py 对齐），
  通过 :func:`packages.core.workflow_registry.get_workflow` 取节点；run 同步执行到底
  （deconstruct 不挂 Human 节点）；失败 → run FAILED，路由仍返回 201（含 status 字段便于前端诊断）。
- list 摘要：从 canon_json 解析 logline + spine 长度 + rhythm 章节数；canon_json 完整 JSON 不展开。
- DELETE：单事务级联删 canon_extracts + reference_canons；FK ON DELETE CASCADE 启用则更稳，
  本实现显式事务删除便于审计。
"""

from __future__ import annotations

import html
import re
import sqlite3
import zipfile
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from packages.core.db import get_connection
from packages.core.logging_config import get_logger
from packages.core.model_router import ModelNotConfiguredError, ModelRouter
from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run
from packages.domain.project.service import ProjectService
from packages.domain.reference import ReferenceService

log = get_logger("novelos.routers.reference")

router = APIRouter(tags=["reference"])

# 拆书文本硬上限：5 MB 字符。超过则 422 拒绝（防 DoS 与内存放大）。
_MAX_DECONSTRUCT_TEXT_LEN = 5_000_000

# V3.x 拆书文件上传（multipart/form-data）：
# - 文件字节硬上限：20 MB（解析前）。
# - 解析后正文字符硬上限：3,000,000 字符（比纯文本端点略低，留出 epub 解析与去标签的余量）。
_MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024
_MAX_UPLOAD_TEXT_LEN = 3_000_000
# F6 修复：epub 内单文件大小上限（5 MB）。epub 容器大小已被 _MAX_UPLOAD_FILE_BYTES
# (20 MB) 兜底；此处再加单文件维度是为了防止「容器本身小但单个嵌入文件膨胀」
# (如恶意构造的 19 MB 单文件 zip bomb) 一次 ``zf.read`` 直接吃满内存。
_MAX_EPUB_MEMBER_BYTES = 5 * 1024 * 1024

# 去标签正则：匹配 HTML/XML 标签（含自闭合）；用于 epub XHTML 文本提取。
_TAG_PATTERN = re.compile(r"<[^>]+>")
# 多空白归一：连续换行/制表/空格合并为单个换行（保留段落空行）。
_MULTI_WS_PATTERN = re.compile(r"[ \t\f\v]+")
_MULTI_NL_PATTERN = re.compile(r"\n{3,}")

# 拆书工作流涉及的 capability 集合（deconstructor_chapter / deconstructor_aggregate
# 在 packages.core.model_router.AGENT_CAPABILITY 均映射到 "reasoning"）。
_DECONSTRUCT_REQUIRED_CAPABILITIES: tuple[str, ...] = ("reasoning",)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path(request: Request) -> str:
    return str(request.app.state.settings.db_path)


def _engine(request: Request) -> WorkflowEngine:
    return WorkflowEngine(_db_path(request))


def _ensure_project(request: Request, project_id: str) -> None:
    if ProjectService(_db_path(request)).get(project_id) is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )


# ---------------------------------------------------------------------------
# V3.x 拆书文件上传（multipart/form-data）helpers
# ---------------------------------------------------------------------------


def _normalize_ext(filename: str | None) -> str:
    """小写扩展名（带 .）；无扩展名或为空 → 空串。"""
    if not filename:
        return ""
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def _decode_txt(raw: bytes) -> str:
    """txt 解码：utf-8（含 BOM）→ gb18030 → utf-16；都失败抛 UnicodeDecodeError。

    三步链式尝试，覆盖最常见的国产 txt（GBK 系）与跨语言 utf-16。
    **不用 errors="replace"**——静默乱码会污染拆书质量，必须让上层明确报错。

    Raises:
        UnicodeDecodeError: 三种编码都解码失败时原样抛出（路由层转 400）。
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw[3:].decode("utf-8")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        pass
    # utf-16 兜底（含 BOM 头自动识别 LE/BE）。继续失败则上层路由转 400。
    return raw.decode("utf-16")


def _strip_xhtml_to_text(xhtml_bytes: bytes) -> str:
    """把单段 XHTML 内容文档去标签 → 纯文本。

    - 解码失败回退 latin-1（兜底）。
    - 去除所有 HTML/XML 标签（含自闭合），html.unescape 反转义实体。
    - 空白归一：连续空格/制表合并为单空格；连续换行最多保留 2 个（保留段落空行）。
    """
    try:
        raw_text = xhtml_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw_text = xhtml_bytes.decode("latin-1", errors="replace")
    no_tags = _TAG_PATTERN.sub(" ", raw_text)
    unescaped = html.unescape(no_tags)
    unescaped = _MULTI_WS_PATTERN.sub(" ", unescaped)
    unescaped = _MULTI_NL_PATTERN.sub("\n\n", unescaped)
    return unescaped.strip()


def _parse_epub(raw: bytes) -> tuple[str, str | None]:
    """stdlib 解析 epub：zipfile + xml.etree 按 spine 顺序拼接正文。

    返回：(正文, dc:title 兜底)。
    损坏 / 非 epub / 无可读 XHTML → 抛 ValueError 让路由层转 400。
    """
    try:
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            names = zf.namelist()
            # 1) 校验 mimetype：epub 规范要求首文件 mimetype 内容为
            #    "application/epub+zip" 且未压缩（stored）。
            if "mimetype" not in names:
                raise ValueError("不是有效的 epub 文件（缺少 mimetype）")
            mimetype = zf.read("mimetype").decode("ascii", errors="replace").strip()
            if mimetype != "application/epub+zip":
                raise ValueError(
                    f"不是有效的 epub 文件（mimetype={mimetype!r}）"
                )

            # 2) 解析 container.xml 找 OPF 路径。
            container_info = zf.getinfo("META-INF/container.xml")
            container_xml = zf.read(container_info)
            container_root = ET.fromstring(container_xml)
            ns_c = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
            opf_href: str | None = None
            for rootfile in container_root.findall("c:rootfiles/c:rootfile", ns_c):
                full_path = rootfile.attrib.get("full-path")
                if full_path and full_path.lower().endswith(".opf"):
                    opf_href = full_path
                    break
            if not opf_href:
                raise ValueError("epub container.xml 未指向任何 OPF")

            # 3) 解析 OPF：manifest（id→href）+ spine（itemref 顺序）。
            opf_xml = zf.read(opf_href)
            opf_root = ET.fromstring(opf_xml)
            ns_opf = {
                "opf": "http://www.idpf.org/2007/opf",
                "dc": "http://purl.org/dc/elements/1.1/",
            }

            # dc:title 兜底（仅当用户没填 book_title 时使用）。
            # 注：ElementTree.find 在 opf_root 直接 dc:title 时受子元素 namespace
            # 声明边界限制不可靠，统一用 .iter() 遍历带 dc namespace 的 title 元素。
            dc_title: str | None = None
            for el in opf_root.iter():
                if el.tag == f"{{{ns_opf['dc']}}}title" and el.text:
                    dc_title = el.text.strip() or None
                    break

            manifest: dict[str, str] = {}
            for item in opf_root.findall("opf:manifest/opf:item", ns_opf):
                item_id = item.attrib.get("id")
                href = item.attrib.get("href")
                media = (item.attrib.get("media-type") or "").lower()
                if item_id and href and "html" in media:
                    manifest[item_id] = href

            spine_ids: list[str] = []
            for itemref in opf_root.findall("opf:spine/opf:itemref", ns_opf):
                idref = itemref.attrib.get("idref")
                if idref:
                    spine_ids.append(idref)

            if not spine_ids:
                raise ValueError("epub OPF 无可读 spine")

            # 4) 按 spine 顺序拼接正文。处理 OPF 相对路径（含子目录）。
            opf_dir = opf_href.rsplit("/", 1)[0] if "/" in opf_href else ""

            def _resolve(href: str) -> str:
                if opf_dir and not href.startswith("/"):
                    return f"{opf_dir}/{href}"
                return href

            chunks: list[str] = []
            for sid in spine_ids:
                href = manifest.get(sid)
                if not href:
                    continue
                member_name = _resolve(href)
                try:
                    member_info = zf.getinfo(member_name)
                except KeyError:
                    continue
                # F7 修复：epub 含加密内容（zip flag_bits & 0x1）→ 400 拒绝。
                # 我们 stdlib zipfile 没有内建解密器；不解密的/继续读取会出现
                # 解码失败的乱码或直接抛 NotImplementedError，与其把坏数据塞进拆书，
                # 不如让上层明确告知「加密 epub 不在支持范围」。
                if member_info.flag_bits & 0x1:
                    raise ValueError("epub 含加密内容，不支持")
                # F6 修复：单文件大小预检——``getinfo().file_size`` 是 zip central
                # directory 里的字段（未解压原始字节），O(1) 读取无需解压。
                # 直接 ``zf.read`` 不知道解压后多大，提前用 file_size 兜底。
                if member_info.file_size > _MAX_EPUB_MEMBER_BYTES:
                    raise ValueError(
                        f"epub 内含超大文件（{member_info.file_size} > "
                        f"{_MAX_EPUB_MEMBER_BYTES}），请精简后重试"
                    )
                content = zf.read(member_info)
                text = _strip_xhtml_to_text(content)
                if text:
                    chunks.append(text)

            if not chunks:
                raise ValueError("epub spine 中无可提取正文")

            joined = "\n\n".join(chunks)
            if len(joined) > _MAX_UPLOAD_TEXT_LEN:
                raise ValueError(
                    f"epub 解码后正文超过 {_MAX_UPLOAD_TEXT_LEN} 字符（{len(joined)}），请精简后重试"
                )
            return joined, dc_title

    except zipfile.BadZipFile as exc:
        raise ValueError(f"不是有效的 epub 文件（zip 损坏）：{exc}") from exc
    except ET.ParseError as exc:
        raise ValueError(f"epub XML 解析失败：{exc}") from exc
    except KeyError as exc:
        raise ValueError(f"epub 缺少必要成员：{exc}") from exc


def _parse_upload_to_text(
    filename: str | None, raw: bytes
) -> tuple[str, str | None]:
    """根据扩展名把上传文件解析为正文。

    返回：(text, epub_dc_title_兜底)。
    - 扩展名不支持 → ValueError（路由层转 400）。
    - epub 解析失败 → ValueError。
    - 解析后正文超上限 → ValueError。
    """
    ext = _normalize_ext(filename)
    if ext == ".txt":
        try:
            text = _decode_txt(raw)
        except UnicodeDecodeError as exc:
            # 三步解码链都失败 → 明确告诉前端文件编码不在支持范围内
            # （utf-8 / gb18030 / utf-16）。**不用 errors="replace"**——静默乱码
            # 会污染拆书质量，必须让前端明确感知到。
            raise ValueError(
                "无法解码，请确认 txt 为 utf-8/gb18030/utf-16"
            ) from exc
    elif ext == ".epub":
        text, dc_title = _parse_epub(raw)
        return text, dc_title
    else:
        ext_disp = repr(ext) if ext else "（无扩展名）"
        raise ValueError(
            f"不支持的文件扩展名 {ext_disp}；仅支持 .txt / .epub"
        )
    if len(text) > _MAX_UPLOAD_TEXT_LEN:
        raise ValueError(
            f"文件过大：解析后 {len(text)} 字符超过 {_MAX_UPLOAD_TEXT_LEN} 字符上限，建议拆分"
        )
    return text, None


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/deconstruct
# ---------------------------------------------------------------------------


class DeconstructRequest:
    """请求体：``book_title`` / ``text`` / ``reader_profile``（可选）/ ``mock_providers``（可选）。"""

    def __init__(self, **data: Any) -> None:
        self.book_title = data["book_title"]
        self.text = data["text"]
        self.reader_profile = data.get("reader_profile") or "male_fantasy"
        self.mock_providers = data.get("mock_providers") or None


@router.post(
    "/projects/{project_id}/deconstruct",
    status_code=status.HTTP_201_CREATED,
)
def start_deconstruct(
    project_id: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """启动 deconstruct-book workflow（纯文本正文路径）。"""
    _ensure_project(request, project_id)

    book_title = payload.get("book_title")
    text = payload.get("text")
    if not isinstance(book_title, str) or not book_title.strip():
        raise HTTPException(
            status_code=422,
            detail="book_title must be a non-empty string",
        )
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=422,
            detail="text must be a non-empty string",
        )
    # P1-2：文本硬上限（5 MB 字符），防止超大 body / 内存放大 / DoS。
    if len(text) > _MAX_DECONSTRUCT_TEXT_LEN:
        raise HTTPException(
            status_code=422,
            detail=(
                f"text exceeds {_MAX_DECONSTRUCT_TEXT_LEN} chars (got {len(text)}); "
                f"split into smaller chunks"
            ),
        )
    reader_profile = payload.get("reader_profile") or "male_fantasy"
    mock_providers = payload.get("mock_providers") or None

    return _start_deconstruct_internal(
        project_id=project_id,
        request=request,
        book_title=book_title.strip(),
        text=text,
        reader_profile=reader_profile,
        mock_providers=mock_providers,
    )


def _start_deconstruct_internal(
    *,
    project_id: str,
    request: Request,
    book_title: str,
    text: str,
    reader_profile: str,
    mock_providers: dict[str, Any] | None,
) -> dict[str, Any]:
    """拆书 workflow 启动 + 响应装配的统一内部入口。

    纯文本端点（POST /deconstruct）与上传端点（POST /deconstruct-upload）
    均复用本函数，避免业务逻辑复制。

    - 输入已校验（``book_title`` / ``text`` 非空且不超过 ``_MAX_DECONSTRUCT_TEXT_LEN``，
      ``mock_providers`` 类型已 normalize）。
    - mock 路径（``mock_providers`` 非 None）跳过 ModelRouter capability 检查。
    - 同步执行到底（deconstruct-book 不挂 Human 节点），返回与 POST /deconstruct 同构响应。
    """
    workflow = get_workflow("deconstruct-book")
    if workflow is None:
        raise HTTPException(
            status_code=500,
            detail="workflow 'deconstruct-book' not registered",
        )

    # P1-4：拆书工作流启动前预检查 capability 是否有可用模型。
    # mock 路径（请求体带 ``mock_providers``）不依赖 model_config，跳过此检查；
    # 生产路径（无 mock_providers）必须预检查 capability 是否可用，
    # 在启动 run 之前就 422 拒绝，避免启动一个注定 FAILED 的 run + 兜底 ai_call_logs。
    if not mock_providers:
        router = ModelRouter(_db_path(request))
        for cap in _DECONSTRUCT_REQUIRED_CAPABILITIES:
            if not router.list_enabled(cap):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"no enabled model configured for capability={cap!r}; "
                        f"请先在 AI 设置里启用模型"
                    ),
                )

    initial_ctx: dict[str, Any] = {
        "db_path": _db_path(request),
        "project_id": project_id,
        "book_title": book_title,
        "text": text,
        "reader_profile": reader_profile,
    }
    if mock_providers:
        initial_ctx["mock_providers"] = mock_providers

    try:
        run_id = _engine(request).start_with_nodes(
            "deconstruct-book",
            workflow["nodes"],
            chapter_id=None,
            initial_ctx=initial_ctx,
            mock_providers=mock_providers,
            checkpoint_exclude=["text"],
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # P1-4：未配置可用模型时（deconstructor_* 走 ModelRouter.resolve），从 500 兜成 422
    # 让前端明确「未配置模型」而非不可恢复的服务错误。
    except ModelNotConfiguredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run = get_run(_db_path(request), run_id)
    if run is None:
        raise HTTPException(status_code=500, detail="run disappeared after start")

    out: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
        "project_id": project_id,
        "book_title": book_title,
    }
    # 如 COMPLETED，把 canon_id + extracts_count 提取出来便于前端
    if run["status"] == "COMPLETED":
        ckpt = run.get("checkpoint_json") or {}
        persist = ckpt.get("T4_persist") if isinstance(ckpt, dict) else None
        if isinstance(persist, dict):
            if "canon_id" in persist:
                out["canon_id"] = persist["canon_id"]
            if "extracts_count" in persist:
                out["extracts_count"] = persist["extracts_count"]
    if run["status"] == "FAILED":
        out["error"] = run.get("error")
    return out


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/deconstruct-upload
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/deconstruct-upload",
    status_code=status.HTTP_201_CREATED,
)
def start_deconstruct_upload(
    project_id: str,
    request: Request,
    file: UploadFile = File(..., description="参照书文件（.txt / .epub）"),
    book_title: str = Form("", description="书名；留空则从 epub dc:title 兜底"),
    reader_profile: str = Form("male_fantasy", description="读者档"),
) -> dict[str, Any]:
    """上传 .txt / .epub 启动 deconstruct-book workflow（multipart/form-data）。

    - 必填字段：``file``（.txt / .epub，<= 20 MB）。
    - 可选字段：``book_title``（留空时若为 epub 自动读 dc:title）、``reader_profile``（默认 male_fantasy）。
    - 解析后正文硬上限 3,000,000 字符；超出 → 400 提示精简/拆分。
    - 解析成功后**复用既有 deconstruct 启动逻辑**：走 ``_start_deconstruct_internal``
      与 POST /deconstruct 同一条路径，不引入独立业务分支。
    """
    _ensure_project(request, project_id)

    # 1) 读取文件字节（受 _MAX_UPLOAD_FILE_BYTES 上限约束）。
    raw = file.file.read(_MAX_UPLOAD_FILE_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"文件过大：超过 {_MAX_UPLOAD_FILE_BYTES // (1024 * 1024)} MB 上限，"
                f"请精简后再上传"
            ),
        )

    # 2) 按扩展名解析为正文；epub 时同时拿 dc:title 兜底。
    try:
        text, epub_title_fallback = _parse_upload_to_text(file.filename, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not text.strip():
        raise HTTPException(
            status_code=400,
            detail="文件解析后正文为空，请检查文件内容",
        )

    # 3) 书名兜底链：表单 book_title > epub dc:title > 422。
    title = book_title.strip() if isinstance(book_title, str) else ""
    if not title and epub_title_fallback:
        title = epub_title_fallback.strip()
    if not title:
        raise HTTPException(
            status_code=422,
            detail=(
                "book_title 不能为空；epub 也未读取到 dc:title，"
                "请显式填写 book_title 表单字段"
            ),
        )

    # 4) reader_profile 简单归一（缺省 → male_fantasy）；与纯文本端点行为一致。
    rp = (reader_profile or "male_fantasy").strip() or "male_fantasy"

    # 5) 复用既有拆书 workflow 启动路径。
    return _start_deconstruct_internal(
        project_id=project_id,
        request=request,
        book_title=title,
        text=text,
        reader_profile=rp,
        mock_providers=None,  # 上传端点不暴露 mock（与文件上传的「真实验证」定位一致）
    )


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/canons
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/canons")
def list_project_canons(project_id: str, request: Request) -> list[dict[str, Any]]:
    """列出项目下全部 active canon（按 created_at DESC）。"""
    _ensure_project(request, project_id)
    svc = ReferenceService(_db_path(request))
    return svc.list_active_summaries(project_id)


# ---------------------------------------------------------------------------
# GET /canons/{canon_id}
# ---------------------------------------------------------------------------


@router.get("/canons/{canon_id}")
def get_canon(canon_id: str, request: Request) -> dict[str, Any]:
    """单一 canon 全文（canon_json + report_md + extracts 列表）。"""
    svc = ReferenceService(_db_path(request))
    detail = svc.get_canon_detail(canon_id)
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"canon {canon_id!r} not found"
        )
    return detail


# ---------------------------------------------------------------------------
# DELETE /canons/{canon_id}
# ---------------------------------------------------------------------------


@router.delete("/canons/{canon_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_canon(canon_id: str, request: Request) -> None:
    """级联删除 canon + 关联 extracts（事务）。"""
    svc = ReferenceService(_db_path(request))
    ok = svc.delete_canon_cascade(canon_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"canon {canon_id!r} not found"
        )
    return None


__all__ = ["router"]


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/canons/{canon_id}/to-style-sample
# ---------------------------------------------------------------------------


# 合成后风格卡长度硬控：<= 4800 字符（5000 字上限 + 200 余量）。
# 越界则从 techniques 列表尾部裁剪。
_MAX_SYNTHESIZED_CONTENT_CHARS = 4800


def _format_pct(v: float) -> str:
    """比例字段（0-1）格式化为两位小数百分数；保留数值形态便于校验。"""
    try:
        return f"{round(float(v), 2):.2f}"
    except (TypeError, ValueError):
        return ""


def _render_distribution(d: object, unit: str) -> str | None:
    """sentence_length_distribution / paragraph_length_distribution 渲染为单行指引句。"""
    if not isinstance(d, dict):
        return None
    mean = d.get("mean")
    median = d.get("median")
    mx = d.get("max")
    parts: list[str] = []
    if isinstance(mean, (int, float)):
        parts.append(f"均值 {mean}")
    if isinstance(median, (int, float)):
        parts.append(f"中位 {median}")
    if isinstance(mx, (int, float)):
        parts.append(f"峰值 {mx}")
    if not parts:
        return None
    return f"{unit}分布：{'，'.join(parts)}"


def _build_style_card(
    canon_json: dict[str, Any],
    book_title: str,
) -> tuple[str, str]:
    """把 canon_json 合成 (title, content)。

    - 标题：``《{book_title}》拆书风格卡``
    - 正文：logline + style_params 子字段逐条转写为指引句 + techniques 要点列表
    - 长度硬控 <= 4800 字符；越界按 techniques 列表尾部裁剪
    - 仅消费抽象结论字段，不混入参考书原文片段（与 deconstruct G-sim 阻断同精神）
    """
    title = f"《{book_title}》拆书风格卡"

    logline = canon_json.get("logline") if isinstance(canon_json, dict) else None
    style_params = canon_json.get("style_params") if isinstance(canon_json, dict) else None
    techniques = canon_json.get("techniques") if isinstance(canon_json, dict) else None

    lines: list[str] = []

    if isinstance(logline, str) and logline.strip():
        lines.append(f"logline：{logline.strip()}")
        lines.append("")

    # 风格参数：按固定字段顺序遍历，缺则跳过
    if isinstance(style_params, dict):
        pov = style_params.get("pov")
        if isinstance(pov, str) and pov.strip():
            lines.append(f"叙事视角：{pov.strip()}")

        dialogue_ratio = style_params.get("dialogue_ratio")
        if isinstance(dialogue_ratio, (int, float)):
            v = _format_pct(dialogue_ratio)
            if v:
                lines.append(f"对话比例：{v}")

        action_ratio = style_params.get("action_ratio")
        if isinstance(action_ratio, (int, float)):
            v = _format_pct(action_ratio)
            if v:
                lines.append(f"动作比例：{v}")

        psychological_ratio = style_params.get("psychological_ratio")
        if isinstance(psychological_ratio, (int, float)):
            v = _format_pct(psychological_ratio)
            if v:
                lines.append(f"心理比例：{v}")

        environment_ratio = style_params.get("environment_ratio")
        if isinstance(environment_ratio, (int, float)):
            v = _format_pct(environment_ratio)
            if v:
                lines.append(f"环境比例：{v}")

        sent = _render_distribution(style_params.get("sentence_length_distribution"), "句长")
        if sent:
            lines.append(sent)

        para = _render_distribution(
            style_params.get("paragraph_length_distribution"), "段落",
        )
        if para:
            lines.append(para)

    # techniques 要点列表
    tech_lines: list[str] = []
    if isinstance(techniques, list):
        for t in techniques:
            if not isinstance(t, dict):
                continue
            name = t.get("name_pattern") if isinstance(t.get("name_pattern"), str) else None
            loc = t.get("location_pattern") if isinstance(t.get("location_pattern"), str) else None
            eff = t.get("effect_pattern") if isinstance(t.get("effect_pattern"), str) else None
            # 三者全缺 → 跳过该条
            if not (name or loc or eff):
                continue
            seg_parts: list[str] = []
            if name:
                seg_parts.append(name)
            if loc:
                seg_parts.append(f"（{loc}）")
            text = "".join(seg_parts)
            if eff:
                if text:
                    text += f"：{eff}"
                else:
                    text = eff
            tech_lines.append(f"- {text}")

    if tech_lines:
        lines.append("写作技法：")
        lines.extend(tech_lines)

    content = "\n".join(lines).rstrip() + "\n"

    # 总长度硬控：超限按 techniques 列表尾部裁剪
    if len(content) > _MAX_SYNTHESIZED_CONTENT_CHARS:
        # 找到"写作技法："标题所在索引
        tech_header_idx: int | None = None
        for i, ln in enumerate(lines):
            if ln == "写作技法：":
                tech_header_idx = i
                break
        # 从尾部成对移除"- ..."行，保留写作技法标题
        while (
            tech_header_idx is not None
            and len(content) > _MAX_SYNTHESIZED_CONTENT_CHARS
            and tech_lines
        ):
            tech_lines.pop()
            # 重新合成 lines 的 techniques 段
            if tech_header_idx is not None:
                # 移除原"写作技法："后所有列表项，再重新追加
                del lines[tech_header_idx + 1 :]
                if tech_lines:
                    for tl in tech_lines:
                        lines.append(tl)
            content = "\n".join(lines).rstrip() + "\n"
        # 兜底：仍超限则硬截断
        if len(content) > _MAX_SYNTHESIZED_CONTENT_CHARS:
            content = content[:_MAX_SYNTHESIZED_CONTENT_CHARS]

    return title, content


@router.post(
    "/projects/{project_id}/canons/{canon_id}/to-style-sample",
    status_code=status.HTTP_201_CREATED,
)
def write_canon_to_style_sample(
    project_id: str,
    canon_id: str,
    request: Request,
) -> dict[str, Any]:
    """把拆书 canon 合成文风样例卡并落入 author_style_samples。

    - 404：project / canon 不存在，或 canon 不属于该项目
    - 400：canon 的 style_params 字段缺失或类型非法（无法合成）
    - 409：该项目下已有同标题样例（按 title 判重，与 canon_id 不强绑）
    - 422 / 201：来自 author_style_samples 既有校验（≤5000 字 / ≤10 篇）
    """
    _ensure_project(request, project_id)

    svc = ReferenceService(_db_path(request))
    detail = svc.get_canon_detail(canon_id)
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"canon {canon_id!r} not found",
        )
    if detail.get("project_id") != project_id:
        raise HTTPException(
            status_code=404, detail=f"canon {canon_id!r} not found",
        )

    canon_json = detail.get("canon_json") or {}
    if not isinstance(canon_json, dict):
        canon_json = {}

    style_params = canon_json.get("style_params")
    # 既要非 dict 也要空 dict：缺字段 / 类型非法 / 空字典 → 一律 400
    if not isinstance(style_params, dict) or len(style_params) == 0:
        raise HTTPException(
            status_code=400,
            detail="该 canon 无 style_params，无法生成风格卡",
        )

    # book_title 兜底链：detail.title > metadata.source_book_title > 未命名
    book_title = detail.get("title") or ""
    if not book_title:
        metadata = canon_json.get("metadata") or {}
        if isinstance(metadata, dict):
            bt = metadata.get("source_book_title")
            if isinstance(bt, str):
                book_title = bt
    if not book_title:
        book_title = "(未命名)"

    title, content = _build_style_card(canon_json, book_title)

    # 判重：按 title（与 canon_id 不强绑）
    conn = get_connection(_db_path(request))
    try:
        existing = conn.execute(
            "SELECT 1 FROM author_style_samples WHERE project_id = ? AND title = ?",
            (project_id, title),
        ).fetchone()
    finally:
        conn.close()
    if existing is not None:
        # 主控裁决（P1 接受不修）：to-style-sample 409 判重竞态——同一项目下
        # 用户对同一 canon 两次点击「写入文风样例面板」，因前端 ``writeBusy``
        # 防抖 + 单用户 UI + 标题维度判重（不依赖 canon_id），竞态窗口不
        # 暴露给用户；业务上也允许「同一 canon 重写覆盖旧样例」时让用户先
        # 手动删除旧样例。如未来需要严格防重，需在此处加 ``SELECT ... FOR
        # UPDATE`` 或 UNIQUE(project_id, title) 约束。
        raise HTTPException(
            status_code=409,
            detail=f"该 canon 风格卡已写入过（标题={title!r}）",
        )

    # 复用既有 author_style_samples 创建端点（含 5000 字 / 10 篇校验与 INSERT 路径一致）
    from packages.core.api.routers.author_style_samples import (
        StyleSampleCreate,
        create_style_sample,
    )

    payload = StyleSampleCreate(title=title, content=content)
    return create_style_sample(project_id, payload, request)
