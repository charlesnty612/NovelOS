// 改稿指令（2026-10-15）。
//
// 后端 `PATCH /chapters/{cid}/revision-note` 一直可用，但前端从没有入口——作者想改稿
// 只能在门禁/审校停住之后，从审批框或「重开返修」里把意见倒着塞回去。补齐这个入口的价值
// 在于**提前写**：改稿指令是下一次写正文的输入，先写下来比事后补一句强。
//
// ⚠️⚠️⚠️ **这一格是覆盖式的，绝不能呈现成「作者笔记 / 意见箱 / 意见历史」**——
//   全仓有四个写入方，写的都是同一个键 `chapters.plan_json["revision_note"]`：
//     ① 本端点 `ChapterService.update_revision_note`（写新值，**覆盖**）
//     ② `workflows/chapter_review/pipeline.py:1117` 驳回时落 human_input.note
//     ③ `workflows/chapter_commit/gate.py:288` 门禁阻断/confirm 时落改稿出路
//     ④ `reopen_for_repair` 重开返修时填的理由（`reopen: ` 前缀，**换行追加**，不覆盖）
//   后写覆盖先写、无历史、无版本。把它画成意见箱就是在骗作者（他以为驳回意见都留着）。
//   ⇒ 正确呈现是「**改稿指令**」+ 显示当前这一格 + 说清它可能是上一次驳回时写进去的。
//
// ⚠️ **它自己不会触发改稿**：`chapter_write/pipeline.py:316-331` 只在下一次 write run
//   里读它——且必须**本章已有 draft** 才会走 `mode='revise'`；否则照常新写。
//   写完这一格不去发起那一次，本章不会变。
//
// ⚠️ 章节状态 ∉ {PLANNED, DRAFTED, REVIEWED} 时后端 409（`_REVISION_NOTE_ALLOWED_STATUS`，
//   COMMITTED / RELEASED 定稿锁死）⇒ 表单禁用并说明出路，不让作者填完再撞 409。
//
// ⚠️ 提交纯空白 = **删键**（不是存空串，`update_revision_note` 走 `plan.pop`）⇒ 删除
//   走独立确认框。
//
// **显示内容的唯一来源**：`GET /chapters/{cid}` 返回的 plan_json。没有读取用的 list 端点，
//   也**没有**任何按章列出历史指令的接口——本组件不自造时间线，因为那会是假的。

import { useCallback, useEffect, useState } from 'react';
import { chaptersApi } from '../../api/endpoints';
import { ApiError } from '../../api/client';
import type { Chapter, ChapterStatus } from '../../api/types';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { ErrorBanner } from '../../components/ErrorBanner';

/** 与后端 `_REVISION_NOTE_ALLOWED_STATUS` 逐字同形：多写一个状态 = 给作者一个假承诺。 */
export const EDITABLE_STATUSES: ReadonlySet<ChapterStatus> = new Set<ChapterStatus>([
  'PLANNED',
  'DRAFTED',
  'REVIEWED',
]);

/** plan_json 是 `Record<string, unknown>`，非字符串一律当空（宁可少显示，不显示错东西）。 */
export function readRevisionNote(plan: Record<string, unknown> | null | undefined): string {
  const v = plan?.revision_note;
  return typeof v === 'string' ? v : '';
}

export interface ChapterRevisionNoteSectionProps {
  chapter: Chapter;
  /** 保存成功后由父页面重拉 chapter（plan_json 已变，父页面那份副本已过期）。 */
  onSaved?: (chapter: Chapter) => void | Promise<void>;
}

export function ChapterRevisionNoteSection({
  chapter,
  onSaved,
}: ChapterRevisionNoteSectionProps) {
  const propNote = readRevisionNote(chapter.plan_json);
  const [note, setNote] = useState(propNote);
  /** 记住「上次见到的 prop 值」：**只跟 prop 比**，不跟「我同步出去的值」比——
      后者会在保存成功后立刻把刚存的值用父页面的旧快照盖回去（父页面还没重拉），
      表现为作者点完保存，框里的字凭空消失。 */
  const [lastPropNote, setLastPropNote] = useState(propNote);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (propNote !== lastPropNote) {
      setLastPropNote(propNote);
      setNote(propNote);
    }
  }, [propNote, lastPropNote]);

  const editable = EDITABLE_STATUSES.has(chapter.status);
  const locked = !editable || saving;
  const trimmed = note.trim();
  /** 提交空白 = 删键。只有当前确实存着东西时，清空才是一次真操作。 */
  const isDelete = trimmed === '' && propNote !== '';
  const dirty = trimmed !== propNote;
  const canSubmit = editable && dirty;

  const save = useCallback(
    async (payload: string) => {
      setErr(null);
      setSaving(true);
      try {
        const next = await chaptersApi.setRevisionNote(chapter.chapter_id, payload);
        // 以服务端回读为准：后端存的是原样 payload（空白 ⇒ 删键），本地不要乐观更新。
        const stored = readRevisionNote(next.plan_json);
        setNote(stored);
        setFlash(stored === '' ? '已清空：这一格现在是空的' : '已保存');
        await onSaved?.(next);
      } catch (e: unknown) {
        // 409 = 状态已变成定稿（父页面拿的是旧快照）；文案要指向出路而不是报错码。
        if (e instanceof ApiError && e.status === 409) {
          setErr(
            '本章已经定稿，后端拒绝写入（409）。先在上面「重开返修」退回 REVIEWED，再回来写。',
          );
        } else {
          setErr(e instanceof Error ? e.message : '保存改稿指令失败');
        }
      } finally {
        setSaving(false);
      }
    },
    [chapter.chapter_id, onSaved],
  );

  return (
    <div className="card" data-testid="chapter-revision-note-section">
      <div className="detail-pane__title">改稿指令（这一格只留一句）</div>

      {/* 诚实说明：常驻。这里最容易骗到作者的就是「意见箱」式的呈现。 */}
      <div className="alert alert--info" data-testid="revision-note-honest">
        <b>这里只有一格</b>：写上新的，刚才那句立刻没了——没有留档、没有历史、也没有版本。
        <br />
        不止你一个地方在写它：审校驳回时你在审批框里写的意见、提交被拦下时系统写的改稿出路、
        「重开返修」时填的理由（会带 <code>reopen: </code> 开头，追加在这一格下面），
        落的都是这一格。{'\n'}
        所以下面这段，可能不是你刚才写的。
        <br />
        <b>它自己不会改稿</b>，只在你下次发起「写正文」时被读到：那时若本章已经有正文，
        writer 会走「按意见改稿」；没有正文就照常新写一遍。写完记得自己再去发起那一次。
        <br />
        清空后提交 = 把这一格<b>删掉</b>，不是存一条空指令。
      </div>

      <ErrorBanner>{err}</ErrorBanner>
      {flash ? (
        <div className="muted small" data-testid="revision-note-flash">
          {flash}
        </div>
      ) : null}

      {!editable ? (
        <div className="muted small" data-testid="revision-note-locked">
          本章状态是 {chapter.status}，已经定稿，后端锁死了这一格（写入会 409）。
          {chapter.status === 'COMMITTED'
            ? '先在上面点「重开返修」退回 REVIEWED，就可以回来写指令。'
            : '要改得先把它退回未锁定状态（RELEASED 先撤销发布标记），再走返修流程。'}
        </div>
      ) : null}

      <textarea
        className="input"
        style={{ width: '100%', minHeight: 120 }}
        disabled={locked}
        aria-label="改稿指令"
        placeholder="例：把第二章的视角从林昭切回周野；结尾别再解释一遍伏笔。"
        value={note}
        data-testid="revision-note-input"
        onChange={(e) => setNote(e.target.value)}
      />

      <div className="muted small">
        {isDelete ? (
          <span data-testid="revision-note-delete-hint">
            现在提交会<b>删掉</b>当前这一格。
          </span>
        ) : trimmed === '' ? (
          '当前这一格是空的。'
        ) : (
          <>当前这一格存着 {propNote === '' ? '空' : '内容'}，保存后会覆盖它。</>
        )}
      </div>

      <div className="cdp-gate-banner__actions" style={{ marginTop: 8 }}>
        <button
          className="btn btn--primary"
          disabled={!canSubmit}
          data-testid="revision-note-save"
          onClick={() => {
            if (isDelete) setConfirming(true);
            else void save(trimmed);
          }}
        >
          {saving ? '保存中…' : '保存改稿指令'}
        </button>
        <button
          className="btn"
          disabled={locked || propNote === ''}
          data-testid="revision-note-clear"
          title="清空这一格"
          onClick={() => setConfirming(true)}
        >
          清空指令
        </button>
      </div>

      <ConfirmDialog
        open={confirming}
        title="删掉这一格？"
        body="提交空白等于删除本章当前的改稿指令。删掉之后，这一格里什么都不剩，下一次写正文就看不到任何指示了。"
        confirmText="删掉"
        danger
        testId="revision-note-confirm"
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false);
          // 确认之后才把框清空：取消时一个字都不许动（之前是点按钮就先清框，
          // 取消后框空着而库里还存着——看起来像删掉了，其实没删）。
          setNote('');
          void save('');
        }}
      />
    </div>
  );
}
