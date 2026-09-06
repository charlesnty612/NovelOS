# packages/domain/knowledge

知识权限域服务（V3.3 P0-2）。

## 职责

- `RevealPolicyService`：`reveal_policies` 表 CRUD + 业务规则，对齐迁移
  `database/migrations/0014_knowledge_reveal.sql` 的 DDL。
- 「谁知道什么 / 何时揭示」的权威口径：读者可见性（visibility）与剧中人物
  知情范围（who_knows）分离管理。

## 关键业务规则

- `target_kind` 限定 8 种枚举（character / location / faction / world_rule /
  event / hook / debt / relationship），按 kind 路由到对应实体表做引用完整性
  校验，失败 → `ValidationError`（422）。
- `status` 三态（planned / revealed / ...）；`status='revealed'` 必须显式提供
  `revealed_chapter`（DDL CHECK 只管枚举，service 层强约束）。
- `reveal_by_chapter` / `revealed_chapter` 必须 ≥1（章节号从 1 起）。
- 删除不存在 → `NotFoundError`（404）；DDL 无 FK 级联，删除策略由调用方决定。

## 文件

- `models.py`：Pydantic 模型（Create / Update / Out）。
- `service.py`：`RevealPolicyService`（每方法内 `get_connection` 开连接、
  `try/finally` 关闭；主键 `new_id("rp")`，时间戳 `now_iso()`）。

## REST 入口

见 `packages/core/api/routers/reveal_policies.py`（本包不感知 HTTP）。
