// 章节「策展大纲」（outline_json）编辑区 —— C-41（2026-11-05）。
//
// **为什么要独立一个组件，而不是塞进 ChapterPlanSection**：
// `chapters.plan_json` 是 **generated 面**（planner 输出，`chapter-plan` 每次运行会覆盖），
// `chapters.outline_json` 是 **策展面**（人/脚本写，chapter-plan 不写）——AGENTS 硬规则
// 「一列不得两用」。两者若共用一份编辑 state / 保存动作，任何一次保存都可能把另一面
// 静默覆盖（这正是 0028 迁移拆分该列的根因）。所以本组件只读写 `outline_json`，
// 与 `ChapterPlanSection` 在页面上并排但状态完全独立。
//
// 交互照抄 ChapterPlanSection：只读态 ProseText + 编辑态 textarea + JSON 校验。
// 「清空」显式发 `outline_json: null`（后端语义：写 NULL，不是写 `{}`）。

import { useEffect, useState } from 'react';
import { chaptersApi } from '../../api/endpoints';
import type { Chapter } from '../../api/types';
import { ErrorBanner } from '../../components/ErrorBanner';
import { ProseText } from '../../components/ProseText';
import { formatJson, tryParseJsonObject } from '../../utils/format';

export function ChapterOutlineSection({
  chapter,
  onUpdated,
}: {
  chapter: Chapter;
  onUpdated: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState<string>(() =>
    formatJson(chapter.outline_json ?? {}),
  );
  const [err, setErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // chapter 外部刷新后（非编辑态）同步 text——与 plan 面板同款。
  useEffect(() => {
    if (!editing) setText(formatJson(chapter.outline_json ?? {}));
  }, [editing, chapter.chapter_id, chapter.outline_json, chapter.updated_at]);

  const handleSave = async () => {
    setErr(null);
    const parsed = tryParseJsonObject(text);
    if (!parsed.ok) {
      setErr(parsed.error);
      return;
    }
    setSubmitting(true);
    try {
      // 只发 outline_json：PATCH 的 plan_json 键缺失 ⇒ 后端保留 generated 面原值。
      await chaptersApi.update(chapter.chapter_id, {
        outline_json: parsed.value,
      });
      setEditing(false);
      onUpdated();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleClear = async () => {
    setErr(null);
    setSubmitting(true);
    try {
      // 显式 null ⇒ 后端写 NULL（= 未策展），与 `{}`（空计划对象）语义不同。
      await chaptersApi.update(chapter.chapter_id, { outline_json: null });
      setEditing(false);
      setText('');
      onUpdated();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '清空失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel" data-testid="outline-panel">
      <div className="panel__title">
        策展大纲
        <span className="cdp-spacer" />
        {!editing ? (
          <button
            className="btn btn--sm"
            onClick={() => setEditing(true)}
            data-testid="outline-edit"
          >
            编辑
          </button>
        ) : (
          <>
            <button
              className="btn btn--sm"
              onClick={() => {
                setEditing(false);
                setText(formatJson(chapter.outline_json ?? {}));
                setErr(null);
              }}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className="btn btn--sm"
              onClick={() => void handleClear()}
              disabled={submitting}
              data-testid="outline-clear"
              title="清空策展大纲（写 NULL，不影响 planner 生成的章节计划）"
            >
              清空
            </button>
            <button
              className="btn btn--sm btn--primary"
              onClick={() => void handleSave()}
              disabled={submitting}
              data-testid="outline-save"
            >
              {submitting ? '保存中…' : '保存'}
            </button>
          </>
        )}
      </div>

      <div className="muted small cdp-hint">
        这里写的是<strong>你手写的大纲</strong>（章节计划里 planner 生成的那份是另一个槽，
        重新生成计划不会覆盖这里）。盘点节拍、必须落地的情节点时用它。
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {!editing ? (
        <div className="prose-block" data-testid="outline-prose">
          <ProseText text={formatJson(chapter.outline_json) || '（空）'} />
        </div>
      ) : (
        <>
          <div className="muted small cdp-hint">
            保存前会校验 JSON；非对象会被拒绝。留空并保存 = 空对象（若要清空请点「清空」）。
          </div>
          <textarea
            className="cdp-plan-textarea"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={14}
            data-testid="outline-textarea"
          />
        </>
      )}
    </div>
  );
}
