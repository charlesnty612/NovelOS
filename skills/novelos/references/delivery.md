# delivery —— 交付判定 / 签约体检 / 投稿包导出

> **读法**：本文件是「这书能交了吗 / 怎么导出」阶段的唯一依据。
> **必须读到 EOF**（含末尾「本轮约束」）。
> 端点逐字取自 `packages/core/api/routers/delivery.py`、`signing_check.py`、
> `export.py`、`quality.py`（q8-export）。

## 1. 三件东西，各答一个问题

| 工具 | 回答 | 端点 |
|---|---|---|
| 交付判定 | 「这本书**能不能交**？哪几章拖后腿？」 | `GET /api/projects/{id}/delivery-verdict` |
| 签约体检 | 「稿子符不符合**签约/开篇**的硬指标？」 | `GET /api/projects/{id}/signing-check` |
| 投稿包导出 | 「把稿子**拿走**（txt / docx / 投稿包）」 | `GET /api/projects/{id}/export?format=…` |

---

## 2. 交付判定

```bash
python skills/novelos/scripts/novelos.py delivery verdict --project prj_x --pretty
```

### 判定四档（`Verdict`）

| 值 | 含义 |
|---|---|
| `deliverable` | 可交付 |
| `needs_work` | 要改（还没到不可救） |
| `not_deliverable` | 不可交付 |
| `not_a_candidate` | 不是候选（还没写 / 状态不对） |

### 响应结构（`DeliveryVerdictResponse`）

| 字段 | 说明 |
|---|---|
| `project_verdict` | 项目级判定（四档之一） |
| `project_reasons[]` | 项目级理由 |
| `roll_up` | 汇总：`chapter_count` / `written_chapter_count` / `deliverable_count` / `needs_work_count` / `not_deliverable_count` / `not_a_candidate_count` + 四个 id 列表 + `blocking_reason_by_chapter`（每章**首个**降级理由） |
| `chapters[]` | 逐章行：`chapter_id` / `number` / `title` / `status`（流程位置，**不是**判定）/ `verdict` / `blocking_reason` / `draft_version` / `length` / `quality` / `signing` / `reasons[]` |
| `evidence_sources[]` | 证据源可达性（**诚实面：看不到的要说出来**） |
| `signing_check_scope` | 本次体检覆盖范围 |
| `generated_at` | 生成时间 |

### 一条理由（`DeliveryReason`）

| 字段 | 取值 |
|---|---|
| `rule_id` | 规则 / 证据来源标识（**前端按此分支**） |
| `source` | `quality_issue` / `length` / `signing_check` / `chapter_status` / `evidence` |
| `severity` | `error` / `warning` / `info`（**证据自身的严重度**） |
| `status` | 后果档（**决定判定是否降级**）——与质量门禁的 `gate` 同构 |
| `message` | 面向作者的中文说明 |
| `evidence` | 证据摘录（引文片段或数字），可空 |

> **读法**：先看 `project_verdict` 与 `roll_up`；要定位问题就进
> `chapters[].reasons[]` 看 `source` + `message` + `evidence`。
> **`blocking_reason` 是「首个」降级理由**——修完它可能冒出下一个 ⇒ 逐轮推进。

---

## 3. 签约体检

```bash
python skills/novelos/scripts/novelos.py delivery signing-check --project prj_x --pretty
```

响应（`SigningCheckResult`）：

| 字段 | 说明 |
|---|---|
| `items[]` | 每条恒 4 键：`key` / `level` / `detail` / `advice` |
| `summary` | 四档计数：`pass_count` / `warn_count` / `fail_count` / `info_count` |
| `total_chars` | 全书字符数 |
| `word_band_source` | 字数带来源（`"<pack_id>@<version>"` 或 `"default"`） |
| `word_band_rejected` | 该字数带被拒时为 true |
| `genre_opening` | 题材开篇规则机检结果（可空） |

`level` 四档与 `summary` 的四个计数一一对应（`pass` / `warn` / `fail` / `info`）。

> 体检**只读**：不改稿、不写库。`fail` 项要改，走正常的 write/revise 流程。

---

## 4. 导出

```bash
# 整书 txt
python skills/novelos/scripts/novelos.py export --kind txt --project prj_x --out 稿子.txt
# 单章 docx
python skills/novelos/scripts/novelos.py export --kind docx --project prj_x --chapter-no 3 --out ch3.docx
# 投稿包（正文 + 大纲）
python skills/novelos/scripts/novelos.py export --kind submission --project prj_x --out 投稿包.txt
```

### `--kind` → 服务端 `format` 映射

| CLI `--kind` | 服务端 `format` | 产物 |
|---|---|---|
| `txt` | `txt` | 整书或单章 txt（**UTF-8 BOM 开头**） |
| `docx` | `docx` | 整书或单章 docx（最小 OOXML） |
| `submission` | `fanqie` | 投稿包 txt（正文 + 大纲；**忽略 `chapter_no`**） |
| `fanqie` | `fanqie` | 同上（`submission` 的别名） |

**服务端只认 `txt` / `docx` / `fanqie`**；`submission` 是客户端侧的别名。

### 端点细节

- `GET /api/projects/{project_id}/export?format={txt|docx|fanqie}[&chapter_no=N]`
- `format` 非法 ⇒ **400**（消息里会列出允许值）。
- `chapter_no` 只对 `txt` / `docx` 有效；缺参 = 整书。
- `chapter_no` ≤ 0 ⇒ 400。
- 响应是**文件体**，不是 JSON：`text/plain; charset=utf-8`（txt/fanqie）、
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`（docx）。
- 响应头 `Content-Disposition` 用 **RFC 5987 双写法**（ASCII 兜底 + `filename*=UTF-8''…`）
  ⇒ 中文书名不会乱码。
- **该端点刻意不声明 `response_model`**（返回文件体不是 JSON，套模型会给前端
  codegen 造一条假契约）。

**客户端行为**：`export` 会**落盘**并回报 `{"ok":true,"path":"…","bytes":N,
"content_type":"…"}`（默认写到当前目录，文件名 `<project_id>-book.txt` /
`<project_id>-ch3.docx`；用 `--out` 指定）。

### 合规自证 CSV（Q8）

`GET /api/projects/{project_id}/quality/q8-export` ⇒ CSV（**UTF-8 BOM**，Excel 中文不乱码），
列：`chapter_number, chapter_title, chapter_status, ai_chars, human_chars,
human_ratio, evaluated_at`。`human_ratio` 是百分比（1 位小数）。

> **口径自洽**：CSV 里的 `human_ratio` 与质量报告里 Q8 的判定**用同一个函数算**
> ⇒ 导出的数字 ≡ 评估口径。`novelos.py` 未封装此端点（用 `curl` 或 UI）。

---

## 5. 导出前的自检清单

导出**不会**替你做质量判断。导出前先看：

1. `delivery verdict` 的 `project_verdict` —— 是不是 `deliverable`？
2. `roll_up.not_deliverable_count` —— 有几章不可交付？
3. `chapters[].blocking_reason` —— 逐章的首个降级理由。
4. `signing-check` 的 `summary.fail_count` —— 有几项硬伤？

**导出 ≠ 通过。** 文件可以导出，但稿子可能还不合格——**把 verdict 与 reasons 一起
报给作者**，别只说「导出好了」。

---

## 6. 边界

- 导出是**纯读**（不写库、不改状态）。
- 投稿包（`fanqie`）**只含正文 + 大纲**，不含评审报告 / Story State。
- 导出**不清理**任何东西；服务端只生成字节流。
- 导出的文件落在**你指定的路径**（默认当前目录）——**别把书稿提交进 git**。

---

## 本轮约束

1. **先 verdict，后导出**；把 `project_verdict` + `blocking_reason` 一并报给作者。
2. **`not_a_candidate` ≠ `not_deliverable`**：前者是「还没写/状态不对」，
   后者是「写了但不合格」——**别混着说**。
3. **`--kind submission` ⇒ 服务端 `format=fanqie`**，且**忽略 `--chapter-no`**。
4. **导出是文件下载**，不是 JSON ⇒ 客户端落盘；用 `--out` 明确路径。
5. **合规 CSV（q8-export）用 curl 或 UI**，客户端未封装。
6. **别把导出的稿子塞进 git / 上传到外部服务**——数据不出本机。
7. 交付判定的**理由以 `rule_id` 为准**；`source` 告诉你该去哪修
   （质量问题 / 字数 / 体检 / 状态 / 证据缺失）。
