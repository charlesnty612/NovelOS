// 人工评分（2026-10-15）。
//
// 后端 `POST /chapters/{cid}/quality/judge` 一直可用，但前端从没有入口——`QualityPanel`
// 只展示机器给的六子分与 issues，作者读完没有任何地方能留下自己的判断。
//
// ⚠️⚠️ **本面板最容易被误解的一点，UI 第一行就是它**：
//   它只 UPDATE `quality_reports.judge_json` 一列（`QualityService.save_judge_score`
//   取最新一份 report 行做 UPDATE，**不 INSERT 新行**），而质量门禁
//   （`workflows/chapter_commit/gate.py`）与评分引擎（`core/quality/engine.py`）
//   **对 judge 零引用**——它不影响 overall、不进 scores_json、更不能解除任何一条阻断。
//   ⇒ 文案必须说清「这是你自己的记录」，否则作者会以为打完分就能提交。
//
// ⚠️ **必须先跑过 evaluate**：judge 只能挂在**已存在的最新报告行**上，没有报告时
//   后端 422（`save_judge_score` 抛 ValueError「run evaluate first」）。故本组件自带
//   `qualityApi.latest` 预读，拿不到报告就禁用表单，而不是让作者填完再撞 422。
//
// ⚠️ **同槽覆盖**：每次保存直接 UPDATE 同一行，无历史、无版本。UI 不假装是「评分历史」。
//
// ⚠️ 后端对分数**只夹 0-100 不报错**（`v = max(0, min(100, int(v)))`），只有非整数抛
//   ValueError → 422；而 verdict ≤50 字 / top_issues 最多 3 条每条 ≤30 字
//   **后端零校验**（service 层「其余字段原样保留」）。⇒ 这三条约束全部由前端兜住，
//   否则作者会存进一条后端照单全收、下次读出来又超标的记录。

import { useCallback, useEffect, useState } from 'react';
import { qualityApi } from '../../api/endpoints';
import { ApiError } from '../../api/client';
import type {
  QualityJudgePayload,
  QualityJudgeRecord,
  QualityReport,
} from '../../api/types';
import { ErrorBanner } from '../../components/ErrorBanner';
import { Loading } from '../../components/Loading';

export type JudgeDim = 'pacing' | 'style' | 'logic' | 'dialogue';

const DIMS: ReadonlyArray<{ key: JudgeDim; label: string; hint: string }> = [
  { key: 'pacing', label: '节奏', hint: '这一章推进得快还是拖' },
  { key: 'style', label: '文笔', hint: '句子、叙述顺不顺口' },
  { key: 'logic', label: '逻辑', hint: '因果有没有断、有没有前后打架' },
  { key: 'dialogue', label: '对白', hint: '人物说话像不像人' },
];

const VERDICT_MAX = 50;
const ISSUE_MAX = 30;
const ISSUE_SLOTS = 3;

/** 用码点数而非 `.length`：后者把 emoji 计成两个字，「50 字」的承诺会当场破掉。 */
export function charCount(s: string): number {
  return Array.from(s).length;
}

export interface ScoreCheck {
  ok: boolean;
  value: number | null;
  /** 原值越界、已被夹进 0-100（后端也会夹，但夹完不回显，作者会以为自己填的就是存下的）。 */
  clamped: boolean;
  message: string | null;
}

/**
 * 校验 + 归一化单个维度分。**不静默丢弃非法输入**：
 * 非整数/非数字/空 → ok=false 并带原因（后端对非整数直接 422，对越界则静默夹取，
 * 两边行为不一致，所以前端必须自己给作者一个明确说法）。
 */
export function checkScore(raw: string): ScoreCheck {
  const t = raw.trim();
  if (t === '') return { ok: false, value: null, clamped: false, message: '这一项还没填' };
  const n = Number(t);
  if (!Number.isFinite(n)) {
    return { ok: false, value: null, clamped: false, message: '得是数字' };
  }
  if (!Number.isInteger(n)) {
    return { ok: false, value: null, clamped: false, message: '只能是整数' };
  }
  if (n < 0 || n > 100) {
    return { ok: true, value: Math.min(100, Math.max(0, n)), clamped: true, message: null };
  }
  return { ok: true, value: n, clamped: false, message: null };
}

type ScoreInputs = Record<JudgeDim, string>;
type JudgeErrors = Partial<Record<JudgeDim, string>>;

function hydrate(judge: QualityJudgeRecord | null | undefined): {
  scores: ScoreInputs;
  verdict: string;
  issues: string[];
} {
  return {
    scores: {
      pacing: judge?.pacing != null ? String(judge.pacing) : '',
      style: judge?.style != null ? String(judge.style) : '',
      logic: judge?.logic != null ? String(judge.logic) : '',
      dialogue: judge?.dialogue != null ? String(judge.dialogue) : '',
    },
    verdict: typeof judge?.verdict === 'string' ? judge.verdict : '',
    issues: Array.from({ length: ISSUE_SLOTS }, (_, i) =>
      Array.isArray(judge?.top_issues) && typeof judge.top_issues[i] === 'string'
        ? (judge.top_issues[i] as string)
        : '',
    ),
  };
}

export interface ChapterJudgeSectionProps {
  chapterId: string;
  /**
   * 父页面跑完「运行质量评估」后自增，触发重读最新报告。
   * 不传则只在 chapterId 变化时读一次（本组件自带 qualityApi.latest，
   * 父页面同款请求不会共用——需要省这一次 GET 时请由主会话改成受控组件）。
   */
  reloadKey?: number;
  /** 打分落库成功后回调（父页面据此刷新自己那份 QualityReport）。 */
  onJudged?: (judge: QualityJudgeRecord) => void;
}

export function ChapterJudgeSection({
  chapterId,
  reloadKey,
  onJudged,
}: ChapterJudgeSectionProps) {
  const [report, setReport] = useState<QualityReport | null>(null);
  /** 「已经读过一轮」——区分「还在读」与「读完确实没有报告」，否则会闪一次空态。 */
  const [loaded, setLoaded] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [scores, setScores] = useState<ScoreInputs>({
    pacing: '',
    style: '',
    logic: '',
    dialogue: '',
  });
  const [verdict, setVerdict] = useState('');
  const [issues, setIssues] = useState<string[]>(['', '', '']);
  const [fieldErr, setFieldErr] = useState<JudgeErrors>({});
  const [formErr, setFormErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<QualityJudgeRecord | null>(null);
  const [saving, setSaving] = useState(false);

  const applyJudge = useCallback((judge: QualityJudgeRecord | null | undefined) => {
    const h = hydrate(judge);
    setScores(h.scores);
    setVerdict(h.verdict);
    setIssues(h.issues);
    setSaved(judge ?? null);
  }, []);

  const load = useCallback(async () => {
    setLoadErr(null);
    try {
      const r = await qualityApi.latest(chapterId);
      setReport(r);
      applyJudge(r.judge);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 404) {
        // 没有报告是正常状态（还没评审），不当错误；但表单必须禁用。
        setReport(null);
        setSaved(null);
      } else {
        setReport(null);
        setLoadErr(e instanceof Error ? e.message : '加载评审报告失败');
      }
    } finally {
      setLoaded(true);
    }
  }, [chapterId, applyJudge]);

  useEffect(() => {
    setLoaded(false);
    void load();
  }, [load, reloadKey]);

  // 读失败时同样不给表单：宁可不许打，也不把作者送进一个必失败的 422。
  const canJudge = report !== null;
  const locked = !canJudge || saving;

  const submit = async () => {
    setFormErr(null);
    const errs: JudgeErrors = {};
    const next: ScoreInputs = { ...scores };
    for (const d of DIMS) {
      const c = checkScore(scores[d.key]);
      if (!c.ok || c.value === null) {
        errs[d.key] = c.message ?? '不合法';
      } else {
        next[d.key] = String(c.value); // 夹取结果回显，别让作者以为自己填的等于存下的
      }
    }
    setFieldErr(errs);
    if (Object.keys(errs).length > 0) return;

    const v = verdict.trim();
    if (charCount(v) > VERDICT_MAX) {
      setFormErr(`总体结论最多 ${VERDICT_MAX} 字，现在 ${charCount(v)} 字。`);
      return;
    }
    const cleanedIssues = issues.map((s) => s.trim()).filter((s) => s !== '');
    const tooLong = cleanedIssues.find((s) => charCount(s) > ISSUE_MAX);
    if (tooLong !== undefined) {
      setFormErr(`每条问题最多 ${ISSUE_MAX} 字，这条 ${charCount(tooLong)} 字。`);
      return;
    }

    const first = DIMS[0].key;
    const payload: QualityJudgePayload = {
      pacing: Number(next[first]),
      style: Number(next.style),
      logic: Number(next.logic),
      dialogue: Number(next.dialogue),
    };
    // 空值不发：后端对这几项零校验，空串会被原样存进去当"作者写过但没写"。
    if (v) payload.verdict = v;
    if (cleanedIssues.length > 0) payload.top_issues = cleanedIssues;

    setScores(next);
    setSaving(true);
    try {
      const res = await qualityApi.judge(chapterId, payload);
      setSaved(res.judge);
      if (res.judge) onJudged?.(res.judge);
    } catch (e: unknown) {
      setFormErr(e instanceof Error ? e.message : '保存评分失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card" data-testid="chapter-judge-section">
      <div className="detail-pane__title">我的评分（只给自己看）</div>

      {/* 诚实说明：常驻、不折叠、不随状态消失——它是这个面板存在的意义，
          也是唯一一处作者会误读的地方（"打完分就能提交了"）。 */}
      <div className="alert alert--info" data-testid="judge-honest-note">
        <b>这四个分数是你自己的读后感，不是机器打的分。</b>
        它们只写进这一份评审报告里的「人工评分」一格，
        <b>提交审核根本不看这一格</b>——打完分不会让这一章更容易提交，
        也不会放行任何一条拦截。想让门禁放行，只能改正文。
        <br />
        每保存一次就是直接覆盖上一次，没有留档。
      </div>

      <ErrorBanner>{loadErr}</ErrorBanner>
      <ErrorBanner>{formErr}</ErrorBanner>

      {!loaded ? <Loading text="读取评审报告…" /> : null}

      {loaded && report === null && !loadErr ? (
        <div className="muted small" data-testid="judge-no-report">
          这一章还没有评审报告，分数挂不上去（后端只能把分数记在已有的报告上）。
          先在「质量评估」里跑一次「运行质量评估」，再回来打分。下面的表单现在填不了。
        </div>
      ) : null}

      {/* 表单始终渲染：无报告时**真的 disabled**（而不是藏起来）——「打不了分」这件事
          要在 DOM 上看得见，否则作者只会以为这里坏了或还没刷新。 */}
      {loadErr ? null : (
        <>
          {saved ? (
            <div className="muted small" data-testid="judge-saved">
              已保存（同一份报告上覆盖）：{' '}
              {DIMS.map((d) => `${d.label} ${String(saved[d.key])}`).join(' · ')}
              {saved.verdict ? ` · 结论「${saved.verdict}」` : ''}
              {Array.isArray(saved.top_issues) && saved.top_issues.length > 0
                ? ` · 问题：${saved.top_issues.join('；')}`
                : ''}
            </div>
          ) : report !== null ? (
            <div className="muted small" data-testid="judge-unsaved">
              这一份报告还没打过分。
            </div>
          ) : null}

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>维度</th>
                  <th>分数（0-100 整数）</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                {DIMS.map((d) => (
                  <tr key={d.key}>
                    <td>{d.label}</td>
                    <td>
                      {/* 用 text + inputMode 而非 type=number：number 会把「七」静默变成空串，
                          作者永远不知道自己打错了一个字，而越界/非整数本来就该由本组件显式
                          报错或夹取——浏览器那个只会替我们静默处理。 */}
                      <input
                        className="input"
                        type="text"
                        inputMode="numeric"
                        style={{ width: 96 }}
                        aria-label={d.label}
                        disabled={locked}
                        value={scores[d.key]}
                        data-testid={`judge-score-${d.key}`}
                        onChange={(e) =>
                          setScores((s) => ({ ...s, [d.key]: e.target.value }))
                        }
                      />
                      {fieldErr[d.key] ? (
                        <span className="muted small" data-testid={`judge-err-${d.key}`}>
                          {fieldErr[d.key]}
                        </span>
                      ) : null}
                    </td>
                    <td className="muted small">{d.hint}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="muted small" style={{ marginTop: 8 }}>
            <label htmlFor="judge-verdict">总体结论（可留空，最多 {VERDICT_MAX} 字）</label>
            <input
              id="judge-verdict"
              className="input"
              type="text"
              disabled={locked}
              style={{ width: '100%' }}
              value={verdict}
              data-testid="judge-verdict"
              onChange={(e) => setVerdict(e.target.value)}
            />
          </div>

          <div className="muted small" style={{ marginTop: 8 }}>
            最想说的 {ISSUE_SLOTS} 个问题（可留空，每条最多 {ISSUE_MAX} 字；留空的不会提交）
          </div>
          {issues.map((v, i) => (
            <div key={i} className="muted small" style={{ marginTop: 4 }}>
              <input
                className="input"
                type="text"
                aria-label={`问题 ${i + 1}`}
                disabled={locked}
                style={{ width: '100%' }}
                value={v}
                data-testid={`judge-issue-${i}`}
                onChange={(e) =>
                  setIssues((list) => list.map((old, j) => (j === i ? e.target.value : old)))
                }
              />
            </div>
          ))}

          <div className="cdp-gate-banner__actions" style={{ marginTop: 10 }}>
            <button
              className="btn btn--primary"
              disabled={locked}
              data-testid="judge-submit"
              onClick={() => void submit()}
            >
              {saving ? '保存中…' : saved ? '覆盖保存' : '保存评分'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
