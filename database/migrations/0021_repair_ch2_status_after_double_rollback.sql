-- =============================================================================
-- NovelOS Database Migration 0021: 一次性事故修复——ch2 状态校正
--   背景（2026-08-31 生产事故）：《死当》第 2 章（ch_d5a64a584288）的 commit
--   cmt_6c9faba70b8c 被 rollback 两次（rollback 当时无幂等守卫，代码修复见
--   同批次 commits.py 改动）。rollback 不联动 chapter.status，章节卡在
--   COMMITTED，save_draft 拒绝补丁改稿；PATCH COMMITTED→DRAFTED 又被状态机
--   合法转移表拒绝——唯一解锁通路是数据校正。
--
--   校正依据：双重回滚后 story state 实质已回到第 2 章 commit 前
--   （add 类幂等移除；3 条 update 残留原值，经主控评估在 v6 重提交后依然
--   成立，无害，不在本迁移处理），chapter 语义应为 DRAFTED（待改稿重提交）。
--
-- 幂等策略：WHERE 双守卫（chapter_id 精确匹配 + status 必须仍为 COMMITTED），
--   已校正/已重提交的库重跑 0 行影响；apply_migrations 文件粒度追踪为第二重。
-- =============================================================================

UPDATE chapters
SET status = 'DRAFTED',
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE chapter_id = 'ch_d5a64a584288'
  AND status = 'COMMITTED';

-- =============================================================================
-- 迁移结束 (0021)
-- =============================================================================
