"""手工 OOXML docx 打包（V1.4，无 python-docx 依赖）。

为什么不引入 python-docx：
- ``pyproject.toml`` 当前只列出 fastapi/uvicorn/pydantic/jsonschema/httpx/pytest/ruff；
- 本任务书给死「禁止 pip install 新包」；
- 整书/单章导出只需段落 + 二级标题，OOXML 模板可以最小化到 ~300 字节。

实现要点：
- docx 本质是 zip，内含 ``[Content_Types].xml``、``_rels/.rels``、``word/document.xml``；
- ``document.xml`` 用 ``<w:document><w:body>...</w:body></w:document>`` 包段落；
- 段落用 ``<w:p><w:pPr><w:pStyle w:val="..."/></w:pPr><w:r><w:t xml:space="preserve">...</w:t></w:r></w:p>``；
- ``Heading1`` / ``Heading2`` 是 docx 内建样式；``Normal`` 是默认段落样式；
- 文本中 ``<`` / ``>`` / ``&`` 必做 XML 转义，否则生成的 docx 无法被 Word/Python zipfile+ElementTree 解析。

口径：
- 只支持段落级（Heading1/Heading2/Normal），不支持表格、列表、图片、字体属性；
- 输出能被 ``zipfile.is_zipfile`` 识别（合法 zip），且 ``document.xml`` 解析后包含正文字符串
  （任务书验收）。
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_ROOT_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _document_xml(title: str, paragraphs: list[tuple[str, str]]) -> str:
    """组装 word/document.xml 字符串。"""
    # 文档头部设置 core.xml properties 可选；任务书不要求 metadata，跳过。
    pieces: list[str] = []
    pieces.append(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
    )
    for style, text in paragraphs:
        safe = escape(text or "")
        pieces.append(
            '<w:p><w:pPr><w:pStyle w:val="'
            + (style or "Normal")
            + '"/></w:pPr>'
            + '<w:r><w:t xml:space="preserve">'
            + safe
            + "</w:t></w:r></w:p>"
        )
    # sectPr：合法 docx 必须有一个 sectPr；最小可用版本（无页眉页脚）
    pieces.append(
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/>'
        "</w:sectPr>"
    )
    pieces.append("</w:body></w:document>")
    return "".join(pieces)


def build_minimal_docx(
    *,
    title: str,
    paragraphs: list[tuple[str, str]],
) -> bytes:
    """构造最小合法 docx 字节流。

    ``paragraphs`` 每个元素是 ``(style, text)``，style ∈ ``{"Heading1","Heading2","Normal"}``。
    """
    document = _document_xml(title, paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _ROOT_RELS_XML)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


__all__ = ["build_minimal_docx"]
