# exporter（导出发布链路）

> 职责：把 chapter / plan 数据组装成作者可发布的纯文档产物——整书/单章 txt、整书/单章 docx、番茄投稿包（正文 + 大纲）。
> 状态：V1.4（Sprint 16）实现。

## 职责与边界

**做**：
- 整书 txt：项目下全部章节（`number` ASC）拼成 utf-8-sig txt，每章标题 + 正文；
- 单章 txt：按 `chapter_no` 取单章；
- 整书/单章 docx：与 txt 同内容的最小 OOXML docx（手工打包，无需 `python-docx`）；
- 番茄投稿包 txt：前 N 章拼满 ~1 万字正文 + 分隔线 + 全书大纲（章节计划）。
- 只读：全程只 SELECT，不写库、不调 LLM、不引入新 Python 依赖。

**不做**：
- 不生成 epub / pdf / mobi（任务书未要求）；
- 不修改数据库 schema；
- 不重排正文（保留作者原文中所有段落、空行）；
- 不引入富文本样式（docx 仅段落 + 二级标题，无字体/字号/列表/表格）。

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `build_txt(db_path, project_id, scope)` | `packages/core/exporter/builder.py` | utf-8-sig 编码 txt；`scope.kind ∈ {"book","chapter"}` |
| `build_docx(db_path, project_id, scope)` | 同上 | 最小合法 OOXML docx；同段结构 |
| `build_fanqie_package(db_path, project_id)` | 同上 | 前 ~1 万字正文 + 全书大纲 |
| `plan_to_outline(plan_json)` | 同上 | 把 chapter-plan 写回的 `plan_json` dict 渲染成可读大纲 |
| `build_minimal_docx(title, paragraphs)` | `packages/core/exporter/docx.py` | 手工打包 docx 字节流（测试与 builder 共用） |
| `FANQIE_TARGET_CHARS = 10000` | `builder.py` | 番茄包正文目标字数（README 注明） |
| `FANQIE_DELIMITER` | `builder.py` | 正文与大纲之间的分隔串 |

## 取数口径（任务书给死）

- 章节列表：`ChapterService.list_by_project(project_id)` —— `ORDER BY number ASC`；
- 单章正文：`SELECT content FROM drafts WHERE chapter_id = ? ORDER BY version DESC LIMIT 1`；
  无 draft（仅 PLANNED）→ 空串；导出器在 txt/docx 中保留章节标题与 `（本章尚无正文）` 占位。
- 大纲：`chapters.plan_json`（`chapter-plan` 工作流在 Sprint 4-A 起写回）；
  渲染字段：`chapter_goal / core_conflict / turning_point / expected_role / key_beats[*].purpose`；
  容错：缺字段或 plan_json 为空 → 输出 `（暂无）`，单章无 plan 不影响整书大纲生成。
- 番茄投稿包字数：在最近章节边界截断（不切到章中间），目标 10000 字；超过则截断，无则全量。

## 不引入新依赖

- 任务书：禁止 pip install 新包；
- 因此 docx 用标准库 `zipfile` 手工组装最小 OOXML；
- 段落样式仅 `Heading1 / Heading2 / Normal`（docx 内建样式，零自定义样式表）。

## 使用入口

### 代码侧（API router）

```python
from packages.core.exporter import ExportScope, build_docx, build_txt

# 整书 docx
data = build_docx(db_path, project_id, ExportScope(kind="book"))
# 单章 txt
data = build_txt(db_path, project_id, ExportScope(kind="chapter", chapter_no=3))
```

### HTTP 侧

`GET /api/projects/{project_id}/export?format={txt|docx|fanqie}[&chapter_no=...]`

- `format=fanqie` 时忽略 `chapter_no`；
- `chapter_no` 仅单章导出有效（`kind=chapter`）；
- 404 — 项目不存在；400 — 非法 `format`。

## 维护注意点

- **`discover_routers`** 自动发现新路由文件，无需手动注册。
- **BOM**：`build_txt` / `build_fanqie_package` 输出均带 UTF-8 BOM（`\ufeff`），与 q8-export CSV 一致，
  兼容 Excel / 部分 Windows 文本编辑器对中文的乱码。
- **docx 解析**：手工 OOXML 用 `xml.sax.saxutils.escape` 转义 `<>&`，避免正文中含特殊字符时 docx
  被 Word/Python 解析失败。
- **section 限制**：docx `pgSz w=12240 h=15840`（US Letter），如需 A4 可改 `11906x16838`。
- **不动 schema**：导出只读，与现有 33 张业务表无 schema 耦合。