// 章节计划（plan_json）编辑区（从 ChapterDetailPage 拆出，2026-09-13 前端批次 C）。
//
// 只读态用 ProseText 渲染格式化 JSON；编辑态用 textarea，保存前做 JSON.parse 校验。

import { useEffect, useState } from 'react';
import { chaptersApi } from '../../api/endpoints';
import type { Chapter } from '../../api/types';
import { ErrorBanner } from '../../components/ErrorBanner';
import { ProseText } from '../../components/ProseText';
import { formatJson, tryParseJsonObject } from '../../utils/format';

export function ChapterPlanSection({
  chapter,
  onUpdated,
}: {
  chapter: Chapter;
  onUpdated: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState<string>(() => formatJson(chapter.plan_json ?? {}));
  const [err, setErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // chapter 外部刷新后（非编辑态）同步 text
  useEffect(() => {
    if (!editing) setText(formatJson(chapter.plan_json ?? {}));
  }, [editing, chapter.chapter_id, chapter.plan_json, chapter.updated_at]);

  const handleSave = async () => {
    setErr(null);
    const parsed = tryParseJsonObject(text);
    if (!parsed.ok) {
      setErr(parsed.error);
      return;
    }
    setSubmitting(true);
    try {
      await chaptersApi.update(chapter.chapter_id, { plan_json: parsed.value });
      setEditing(false);
      onUpdated();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel" data-testid="plan-panel">
      <div className="panel__title">
        章节计划
        <span className="cdp-spacer" />
        {!editing ? (
          <button className="btn btn--sm" onClick={() => setEditing(true)}>
            编辑
          </button>
        ) : (
          <>
            <button
              className="btn btn--sm"
              onClick={() => {
                setEditing(false);
                setText(formatJson(chapter.plan_json ?? {}));
                setErr(null);
              }}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className="btn btn--sm btn--primary"
              onClick={handleSave}
              disabled={submitting}
              data-testid="plan-save"
            >
              {submitting ? '保存中…' : '保存'}
            </button>
          </>
        )}
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {!editing ? (
        <div className="prose-block" data-testid="plan-prose">
          <ProseText text={formatJson(chapter.plan_json) || '（空）'} />
        </div>
      ) : (
        <>
          <div className="muted small cdp-hint">保存前会校验 JSON；非对象会被拒绝。</div>
          <textarea
            className="cdp-plan-textarea"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={18}
            data-testid="plan-textarea"
          />
        </>
      )}
    </div>
  );
}
