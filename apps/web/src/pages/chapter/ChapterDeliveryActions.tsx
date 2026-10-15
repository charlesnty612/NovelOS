// 章节交付态标记：COMMITTED ⇄ RELEASED（D1，2026-10-15）。
//
// **这条为什么存在**：`RELEASED` 从项目第一天起就写在 `ChapterStatus` Literal、
// `ALLOWED_NEXT`、`chapters.status` 的 CHECK 约束（`0001_init.sql:247`）和
// `ChapterStatusBadge` 的配色表里，`delivery` / `signing_check` 也都把它当
// 「已过提交门」读——但**没有任何一处把它置成 RELEASED**：没有专门的工作流，
// 也没有 UI 入口。评估报告写作「全仓无写入方」，这句我**复算后修正**：
// `ChapterService.update()` 经 `PATCH /chapters/{id}` 就是写入方，
// `ALLOWED_NEXT["COMMITTED"]` 含 `RELEASED`（只特判挡住了 `COMMITTED→REVIEWED`），
// 所以**后端一直支持，缺的只是没人去调**。于是本组件是纯前端补齐，零后端改动。
//
// **删除方案为何不可行**：`RELEASED` 在 `0001_init.sql` 的 CHECK 约束里，
// 删它要重建 `chapters` 表，而迁移纪律是「只加不改」（AGENTS §二 硬规则 6）。
//
// **它是纯记账，不改变任何判定**：`DELIVERED_STATUSES` 与
// `_INCLUDED_STATUSES` 都把 `{COMMITTED, RELEASED}` 当同一档，标记发布不会
// 改变 `delivery-verdict` / `signing-check` 的任何结论。它的价值只有一个：
// 让作者知道**哪几章已经投出去了**——连载作者需要这个，而产物仍然由
// ExportPanel 的投稿包负责，本按钮**不导出、不上传、不做任何外部动作**。
//
// **必须能撤销**：单向前进会把作者的一次误点变成永久错标。`ALLOWED_NEXT` 允许
// `RELEASED → PLANNED`，故提供撤销入口。

import { useState } from 'react';
import { chaptersApi } from '../../api/endpoints';
import type { Chapter } from '../../api/types';
import { ErrorBanner } from '../../components/ErrorBanner';

export function ChapterDeliveryActions({
  chapter,
  busy,
  onChanged,
}: {
  chapter: Chapter;
  /** 有 run 在跑或正在提交时禁用（与「重开返修」同一把锁）。 */
  busy: boolean;
  /** 迁移成功后由父页面重拉 chapter。 */
  onChanged: () => void | Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const releasable = chapter.status === 'COMMITTED';
  const released = chapter.status === 'RELEASED';
  if (!releasable && !released) return null;

  const move = async (to: 'RELEASED' | 'PLANNED') => {
    setErr(null);
    setPending(true);
    try {
      await chaptersApi.update(chapter.chapter_id, { status: to });
      await onChanged();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '状态迁移失败');
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="alert alert--info cdp-banner-gap" data-testid="delivery-section">
      <div className="cdp-banner__title">
        {released ? '本章已标记为已发布' : '本章已提交定稿'}
      </div>
      <div className="muted small cdp-banner__line">
        {released
          ? '这是作者手工维护的交付标记：用于记录这一章已经投放到平台。'
          : '若本章已经投放到平台，可在此标记「已发布」，便于统计哪些章已交付。'}
        <br />
        <b>本标记不改变任何质量判定</b>——交付判定与签约自检把「已提交」与「已发布」
        视为同一档；本按钮也不导出、不上传任何文件。
      </div>
      <ErrorBanner>{err}</ErrorBanner>
      <div className="cdp-gate-banner__actions">
        {releasable ? (
          <button
            className="btn"
            data-testid="release-btn"
            disabled={busy || pending}
            title="标记本章已发布（可撤销）"
            onClick={() => void move('RELEASED')}
          >
            {pending ? '处理中…' : '标记已发布'}
          </button>
        ) : (
          <button
            className="btn"
            data-testid="unrelease-btn"
            disabled={busy || pending}
            title="撤销「已发布」标记（章节回到已提交待处理）"
            onClick={() => void move('PLANNED')}
          >
            {pending ? '处理中…' : '撤销发布标记'}
          </button>
        )}
      </div>
    </div>
  );
}
