// project-init 子组件：当前关卡的编辑器分发（premise 专用编辑器 / world /
// character / outline 结构化编辑器 / 整段 JSON 兜底）。
// V4.0 前端模块化批次从 ProjectInitPanel.tsx 的 ReviewPane 内联分发原样搬出。
import type { Dispatch, SetStateAction } from 'react';
import {
  CharacterEditor,
  FullJsonFallbackEditor,
  OutlineEditor,
  PremiseEditor,
  WorldEditor,
} from '../ProjectInitEditors';
import type { JsonValidityChange } from '../ProjectInitEditors';

export interface InitStageEditorProps {
  stage: string;
  draft: Record<string, unknown>;
  setDraft: Dispatch<SetStateAction<Record<string, unknown>>>;
  /** premise 专用编辑器的独立缓冲（与 draft 共享同一份 dict）。 */
  premiseDraft: Record<string, unknown>;
  setPremiseDraft: Dispatch<SetStateAction<Record<string, unknown>>>;
  /** 必填 / JSON 非法错误（按 stage.key）。 */
  errors: Record<string, string>;
  busy: boolean;
  onJsonValidityChange: JsonValidityChange;
}

export function InitStageEditor({
  stage,
  draft,
  setDraft,
  premiseDraft,
  setPremiseDraft,
  errors,
  busy,
  onJsonValidityChange,
}: InitStageEditorProps) {
  // premise 用专用的 PremiseEditor（共享 draft state）
  const premiseEditor = (
    <PremiseEditor
      premise={premiseDraft}
      setPremise={(updater) => {
        setPremiseDraft((prev) => {
          const next =
            typeof updater === 'function'
              ? (updater as (p: Record<string, unknown>) => Record<string, unknown>)(prev)
              : updater;
          setDraft(next);
          return next;
        });
      }}
      errors={errors}
      busy={busy}
      onJsonValidityChange={onJsonValidityChange}
    />
  );

  switch (stage) {
    case 'premise':
      return premiseEditor;
    case 'world':
      return (
        <WorldEditor
          draft={draft}
          onChange={setDraft}
          errors={errors}
          onJsonValidityChange={onJsonValidityChange}
        />
      );
    case 'character':
      return (
        <CharacterEditor
          draft={draft}
          onChange={setDraft}
          errors={errors}
          onJsonValidityChange={onJsonValidityChange}
        />
      );
    case 'outline':
      return (
        <OutlineEditor
          draft={draft}
          onChange={setDraft}
          errors={errors}
          onJsonValidityChange={onJsonValidityChange}
        />
      );
    default:
      return (
        <FullJsonFallbackEditor
          stage={stage}
          draft={draft}
          onChange={setDraft}
          onJsonValidityChange={onJsonValidityChange}
        />
      );
  }
}
