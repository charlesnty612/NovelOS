// project-init idle 表单「元信息步」：标题 / 题材 / 平台 / 目标字数 / 单章字数 /
// 章节种子数 + 一句话简介 + 作者备注（V4.0 前端模块化批次从 ProjectInitPanel.tsx
// 原样搬出，文案、testid、校验提示不变）。
import { Field } from './InitField';

export interface InitStepMetaProps {
  title: string;
  setTitle: (v: string) => void;
  genre: string;
  setGenre: (v: string) => void;
  platform: string;
  setPlatform: (v: string) => void;
  targetWords: string;
  setTargetWords: (v: string) => void;
  chapterWordCount: string;
  setChapterWordCount: (v: string) => void;
  chapterSeedCount: string;
  setChapterSeedCount: (v: string) => void;
  /** 用户是否手动改过章节种子数（true 时改单章字数/目标字数不再自动重算）。 */
  seedCountManual: boolean;
  logline: string;
  setLogline: (v: string) => void;
  authorNotes: string;
  setAuthorNotes: (v: string) => void;
  busy: boolean;
}

export function InitStepMeta({
  title,
  setTitle,
  genre,
  setGenre,
  platform,
  setPlatform,
  targetWords,
  setTargetWords,
  chapterWordCount,
  setChapterWordCount,
  chapterSeedCount,
  setChapterSeedCount,
  seedCountManual,
  logline,
  setLogline,
  authorNotes,
  setAuthorNotes,
  busy,
}: InitStepMetaProps) {
  return (
    <>
      <div className="form-grid" style={{ marginTop: 4 }}>
        <Field label="标题">
          <input
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            data-testid="project-init-title"
            disabled={busy}
          />
        </Field>
        <Field label="题材">
          <input
            className="input"
            value={genre}
            onChange={(e) => setGenre(e.target.value)}
            data-testid="project-init-genre"
            disabled={busy}
          />
        </Field>
        <Field label="平台" hint="默认「番茄·男频」">
          <input
            className="input"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            data-testid="project-init-platform"
            disabled={busy}
          />
        </Field>
        <Field label="目标字数">
          <input
            className="input"
            inputMode="numeric"
            value={targetWords}
            onChange={(e) => setTargetWords(e.target.value)}
            data-testid="project-init-target-words"
            disabled={busy}
          />
        </Field>
        <Field label="单章字数" hint="建议 2000-4000，默认 3000">
          <input
            className="input"
            inputMode="numeric"
            value={chapterWordCount}
            onChange={(e) => setChapterWordCount(e.target.value)}
            data-testid="project-init-chapter-word-count"
            disabled={busy}
          />
        </Field>
        <Field
          label="章节种子数"
          hint={
            seedCountManual
              ? '已手动设置（1-500，默认 10）'
              : '1-500，默认 10；按目标字数÷单章字数自动推导'
          }
        >
          <input
            className="input"
            inputMode="numeric"
            value={chapterSeedCount}
            onChange={(e) => setChapterSeedCount(e.target.value)}
            data-testid="project-init-chapter-seed-count"
            disabled={busy}
          />
        </Field>
      </div>

      <Field label="一句话简介（必填）" hint="驱动 premise_designer 节点">
        <textarea
          className="input"
          rows={3}
          value={logline}
          onChange={(e) => setLogline(e.target.value)}
          data-testid="project-init-logline"
          disabled={busy}
          style={{ width: '100%', resize: 'vertical' }}
        />
      </Field>

      <Field label="作者备注（可选）">
        <textarea
          className="input"
          rows={2}
          value={authorNotes}
          onChange={(e) => setAuthorNotes(e.target.value)}
          data-testid="project-init-author-notes"
          disabled={busy}
          style={{ width: '100%', resize: 'vertical' }}
        />
      </Field>
    </>
  );
}
