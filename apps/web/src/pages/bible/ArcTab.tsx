// 弧光面板（评估 G6：`GET /projects/{pid}/arc` 一直在算、一直在返回，UI 零入口）。
//
// 四条口径纪律，全部来自后端语义，不是 UI 偏好：
//
// 1. **「弧光」不是实体。** 后端**没有** `arcs` 表——`build_arc_view`
//    （`packages/core/arc/service.py`）是每次请求现算的派生视图，把 chapters /
//    hooks / narrative_debts / reveal_policies / volumes 五张表现场汇总成一屏 dict。
//    因此本面板不缓存、不给「编辑弧光」入口（后端也没有写端点），并把「刷新 = 重算」
//    明说在页面上，避免作者把昨天的数字当今天的现状。
// 2. **charge / payoff 是推断出来的，不是作者声明的结构。** 后端拿
//    `plan_json.key_beats[].purpose` 里的**字符串标记** `[charge]` / `[payoff]` 做正则
//    匹配（`_CHARGE_TAG_RE` / `_PAYOFF_TAG_RE`），没打标记的章一律算「既不蓄力也不兑现」。
//    ⇒ 「没有告警」的真实含义是「那 4 条规则没触发」，**不是**「这本书节奏没问题」。
//    这句话必须写在页面上，否则空告警会被读成体检通过。
// 3. **章节行没有 chapter_id。** 后端在返回前显式 `ch.pop("chapter_id")`
//    （service.py 注释：「不在 JSON 中暴露」）。所以本面板**不做任何点章节跳转**——
//    没有 id 可跳；硬造一个跳转只能指向 `number`，那是假的链接。
//    行键用「章号 + 标题」：章号由 ChapterService 保证项目内不重号，但 DB 层只有
//    **非唯一**索引 `idx_chapters_project_number`，所以把可见的标题并进键里兜一层。
// 4. **空数组是合法状态。** hooks / debts / payoff / reveal_policies 汇总对象后端总返回，
//    但 chapters / volumes / alerts 可以是空数组——渲染成 EmptyState，不白屏。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { arcApi } from '../../api/endpoints';
import type { ArcAlert, ArcChapterRow, ArcView } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { ErrorBanner, InfoBanner } from '../../components/ErrorBanner';
import { Loading } from '../../components/Loading';

interface ArcTabProps {
  projectId: string;
}

/** 空值占位。空白单元格会被读成「这一项是 0」，真相是「这一项没数据」。 */
const DASH = '—';

/**
 * fail 与 warn 在视觉上必须一眼可分。
 * `charge_streak_exceeded`（连续 3 章蓄力未兑现）是唯一的 fail —— 硬信号；
 * 其余三条（payoff 密度 / 伏笔逾期 / 单章 pacing 低）都是 warn，体检项。
 * 中文标签额外加一层：只靠颜色区分时，色弱用户与灰度打印都会把两者读成同一个。
 */
function levelClass(level: ArcAlert['level']): string {
  return level === 'fail' ? 'badge badge--chapter-failed' : 'badge badge--warn';
}

function levelLabel(level: ArcAlert['level']): string {
  return level === 'fail' ? '硬信号' : '提示';
}

/** 卷状态的后端取值是 `active`/`sealed`；未知值原样透出——不猜、不吞。 */
const VOLUME_STATUS_LABEL: Record<string, string> = {
  active: '进行中',
  sealed: '已封卷',
};

function Stat({
  label,
  value,
  hint,
  testId,
}: {
  label: string;
  value: string | number;
  hint: string;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <div className="muted small">{label}</div>
      <div className="stat__value">{value}</div>
      <div className="muted small">{hint}</div>
    </div>
  );
}

export function ArcTab({ projectId }: ArcTabProps) {
  const [view, setView] = useState<ArcView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setView(await arcApi.get(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  // fail 排到 warn 之前：硬信号沉在体检项下面等于没报警。
  // `Array.prototype.sort` 是稳定排序（同档内保持后端的规则序），所以不丢规则顺序信息。
  const alerts = useMemo(
    () =>
      [...(view?.alerts ?? [])].sort(
        (a, b) => (a.level === 'fail' ? 0 : 1) - (b.level === 'fail' ? 0 : 1),
      ),
    [view],
  );

  const volumeTitleById = useMemo(() => {
    const m = new Map<string, string | null>();
    (view?.volumes ?? []).forEach((v) => m.set(v.volume_id, v.title));
    return m;
  }, [view]);

  const volumeLabel = useCallback(
    (c: ArcChapterRow): string => {
      if (c.volume_number == null) return '未挂卷';
      const title = c.volume_id ? volumeTitleById.get(c.volume_id) : null;
      return title ? `第${c.volume_number}卷（${title}）` : `第${c.volume_number}卷`;
    },
    [volumeTitleById],
  );

  if (loading) return <Loading text="正在汇总弧光…" />;

  // 契约上 arcApi.get 必定返回 ArcView；这里兜的是「拿不到任何对象」——白屏比说实话更坏。
  if (!view) {
    return (
      <div className="stack" data-testid="arc-tab">
        <ErrorBanner>{error}</ErrorBanner>
        <EmptyState title="尚未取到弧光数据" hint="点「重新汇总」再试一次。" />
      </div>
    );
  }

  const chapters = view.chapters ?? [];
  const volumes = view.volumes ?? [];
  const revealOverdue = view.reveal_policies?.overdue ?? [];

  return (
    <div className="stack" data-testid="arc-tab">
      <div className="row row--between">
        <div>
          <div className="detail-pane__title">弧光看板</div>
          <div className="muted small">
            汇总于 {view.generated_at}（每次刷新现算，不落库）
          </div>
        </div>
        <button type="button" className="btn btn--small" onClick={() => void load()}>
          重新汇总
        </button>
      </div>

      <ErrorBanner>{error}</ErrorBanner>

      {/* 口径纪律 1：读者必须先知道这不是一张表，否则会把推断当声明。 */}
      <InfoBanner>
        <span data-testid="arc-info-entity">
          「弧光」<b>不是一张表</b>——后端没有 <code>arcs</code> 表，这里是把章节 / 伏笔 /
          叙事债务 / 剧透策略 / 卷五张表<b>现场汇总</b>出来的只读看板，每次请求重算一次。
          看板上的一切都不是你填的，是从这些表现场算出来的。
        </span>
      </InfoBanner>

      {/* 口径纪律 2：charge/payoff 是字符串标记推断，不是作者声明的结构。 */}
      <InfoBanner>
        <span data-testid="arc-info-beat">
          「蓄力 / 兑现」是从章节计划的 <code>key_beats[].purpose</code> 里的
          <code>[charge]</code> / <code>[payoff]</code>{' '}
          <b>字符串标记</b>推断的，不是结构化声明。没打标记的章一律算「既不蓄力也不兑现」，
          所以下面这几列<b>看不见没打标记的节奏</b>。
        </span>
      </InfoBanner>

      <div className="form-grid" data-testid="arc-stats">
        <Stat
          label="payoff 章"
          value={view.payoff.total}
          hint="带 [payoff] 标记的章数"
          testId="arc-stat-payoff"
        />
        <Stat
          label="当前蓄力连击"
          value={view.payoff.charge_streak}
          hint="连续蓄力未兑现章数（≥3 报硬信号）"
          testId="arc-stat-charge-streak"
        />
        <Stat
          label="历史最长连击"
          value={view.payoff.max_charge_streak}
          hint="全章序里最长的一次"
          testId="arc-stat-max-charge-streak"
        />
        <Stat
          label="最近 payoff"
          value={view.payoff.last_payoff_chapter ?? '尚无'}
          hint="最后一个带 [payoff] 的章号"
          testId="arc-stat-last-payoff"
        />
        <Stat
          label="伏笔"
          value={`未闭环 ${view.hooks.open} · 已闭环 ${view.hooks.resolved}`}
          hint={`逾期 ${view.hooks.overdue}`}
          testId="arc-stat-hooks"
        />
        <Stat
          label="叙事债务"
          value={`未还 ${view.debts.open} · 已还 ${view.debts.paid}`}
          hint="含 forgiven"
          testId="arc-stat-debts"
        />
        <Stat
          label="剧透策略"
          value={`计划 ${view.reveal_policies.planned} · 已揭 ${view.reveal_policies.revealed}`}
          hint={`已取消 ${view.reveal_policies.cancelled} · 逾期 ${revealOverdue.length}`}
          testId="arc-stat-reveal"
        />
      </div>

      {/* 本页最有价值的部分：告警。空态也必须解释「空 ≠ 健康」。 */}
      <section>
        <div className="detail-pane__title">告警（{alerts.length}）</div>
        {alerts.length === 0 ? (
          <EmptyState
            title="没有触发告警"
            hint="这不等于节奏健康：告警只有 4 条规则，且蓄力/兑现靠 key_beats 的字符串标记识别——没打标记的章对它们不可见。"
          />
        ) : (
          <ul data-testid="arc-alert-list">
            {alerts.map((a) => (
              // code 由后端 `_compute_alerts` 保证一码一条（4 条规则各 append 至多一次），
              // 所以它是稳定键；不用 index——重排 fail 优先时 index 会跟着错位。
              <li key={a.code} style={{ marginBottom: 8 }} data-testid={`arc-alert-${a.code}`}>
                <span className={levelClass(a.level)}>{levelLabel(a.level)}</span>{' '}
                <span className="small">{a.message}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <div className="detail-pane__title">章节（{chapters.length}）</div>
        {/* 口径纪律 3 落到 DOM 上：没有 chapter_id 就没有跳转目标。
            空白单元格也不允许——「未评分」与「0 分」是两回事。 */}
        <p className="muted small" data-testid="arc-no-chapter-id">
          章节行<b>没有 chapter_id</b>（后端在返回前脱敏），所以这里不做「点章节跳转」——
          没有 id 可以跳，造一个只能指向章号的假链接比不跳更坏。
        </p>
        {chapters.length === 0 ? (
          <EmptyState
            title="还没有章节"
            hint="章节创建后就会出现在这里；蓄力/兑现列要等计划里打了 [charge] / [payoff] 标记才有值。"
          />
        ) : (
          <table className="table" data-testid="arc-chapter-table">
            <thead>
              <tr>
                <th>章</th>
                <th>标题</th>
                <th>payoff</th>
                <th>蓄力</th>
                <th>总分</th>
                <th>节奏</th>
                <th>字数</th>
                <th>所属卷</th>
              </tr>
            </thead>
            <tbody>
              {chapters.map((c) => (
                <tr key={`ch-${c.number}-${c.title}`} data-testid={`arc-chapter-${c.number}`}>
                  <td>第{c.number}章</td>
                  <td>{c.title || DASH}</td>
                  <td data-testid={`arc-chapter-${c.number}-payoff`}>
                    {c.has_payoff_beat ? '有' : DASH}
                  </td>
                  <td data-testid={`arc-chapter-${c.number}-charge`}>
                    {c.charge_beat ? '有' : DASH}
                  </td>
                  <td>{c.overall ?? DASH}</td>
                  <td>{c.pacing ?? DASH}</td>
                  <td>{c.prose_chars ?? DASH}</td>
                  <td>{volumeLabel(c)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {revealOverdue.length > 0 ? (
        <section>
          <div className="detail-pane__title">到期未揭示的剧透策略（{revealOverdue.length}）</div>
          <table className="table" data-testid="arc-reveal-overdue-table">
            <thead>
              <tr>
                <th>目标种类</th>
                <th>目标 id</th>
                <th>计划揭开于</th>
                <th>面向</th>
              </tr>
            </thead>
            <tbody>
              {revealOverdue.map((r) => (
                <tr key={r.policy_id} data-testid={`arc-reveal-${r.policy_id}`}>
                  <td>{r.target_kind}</td>
                  {/* 本视图只给 id，不给名字——原样透出，不猜它是哪个角色/伏笔。 */}
                  <td className="muted small">{r.target_id}</td>
                  <td>第{r.reveal_by_chapter ?? '?'}章前</td>
                  <td className="muted small">{r.audience || DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      <section>
        <div className="detail-pane__title">卷（{volumes.length}）</div>
        {volumes.length === 0 ? (
          <EmptyState
            title="还没有卷"
            hint="卷是可选的组织层；没有卷时章节表的「所属卷」列显示「未挂卷」，不影响其它列。"
          />
        ) : (
          <table className="table" data-testid="arc-volume-table">
            <thead>
              <tr>
                <th>卷</th>
                <th>标题</th>
                <th>状态</th>
                <th>章数</th>
              </tr>
            </thead>
            <tbody>
              {volumes.map((v) => (
                // 卷是**真表**（有 volume_id），与章节行不同——这里用 id 作键是安全的。
                <tr key={v.volume_id} data-testid={`arc-volume-${v.number}`}>
                  <td>第{v.number}卷</td>
                  <td>{v.title ?? DASH}</td>
                  <td>
                    <span className="badge">{VOLUME_STATUS_LABEL[v.status] ?? v.status}</span>
                  </td>
                  <td>{v.chapter_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
