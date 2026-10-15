// 人物关系面板（评估 G6：54 行数据、零 UI 入口）。
//
// 后端 `relationships` 表是**有方向的成对关系**（from_character_id →
// to_character_id + relation_type），由 State Delta 的 `relationship_changes`
// 驱动写入；S1 只提供只读端点 `GET /projects/{pid}/relationships`。
//
// ⚠️ 与 CharacterTab 里那个可编辑的 `relationships` **不是同一个东西**：
//   - 本面板 = `relationships` **表**，成对、有方向、只读、由 delta 驱动；
//   - CharacterTab = 角色 core JSON 里的 `relationships` **自由字段**，作者手写。
// 同名不同物。本面板**不提供编辑**（后端也没有写端点）——若在此加输入框，
// 写进去的作者手写值与 delta 驱动的真实关系会分叉，且没有任何机制同步它们。
//
// 「谁知道秘密」的维护在 `reveal_policies`（另一条能力，本批不做 UI，见 CHANGELOG）。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { charactersApi, relationshipsApi } from '../../api/endpoints';
import type { Character, Relationship } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { ErrorBanner, InfoBanner } from '../../components/ErrorBanner';
import { Loading } from '../../components/Loading';

interface RelationshipsTabProps {
  projectId: string;
}

/** 关系类型的展示名。未知类型原样透出——不猜、不吞（后端可扩展 relation_type）。 */
function relationLabel(t: string): string {
  return t || '（未标注关系）';
}

export function RelationshipsTab({ projectId }: RelationshipsTabProps) {
  const [rows, setRows] = useState<Relationship[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 筛选：只看某个角色参与的
  const [focus, setFocus] = useState<string>('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rs, cs] = await Promise.all([
        relationshipsApi.list(projectId),
        charactersApi.listByProject(projectId),
      ]);
      setRows(rs);
      setCharacters(cs);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const nameById = useMemo(() => {
    const m = new Map<string, string>();
    characters.forEach((c) => m.set(c.character_id, c.name));
    return m;
  }, [characters]);

  const nameOf = useCallback(
    (cid: string) => nameById.get(cid) ?? `（未知角色 ${cid}）`,
    [nameById],
  );

  const visible = useMemo(
    () =>
      focus ? rows.filter((r) => r.from_character_id === focus || r.to_character_id === focus) : rows,
    [rows, focus],
  );

  if (loading) return <Loading text="正在读取人物关系…" />;

  return (
    <div className="stack">
      <ErrorBanner>{error}</ErrorBanner>

      <div className="row row--between">
        <h3>人物关系（{rows.length}）</h3>
        <select
          className="input"
          aria-label="按角色筛选"
          data-testid="relationship-focus"
          value={focus}
          onChange={(e) => setFocus(e.target.value)}
        >
          <option value="">全部角色</option>
          {characters.map((c) => (
            <option key={c.character_id} value={c.character_id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      <InfoBanner>
        关系是<b>成对且有方向</b>的（如「甲 → 乙：盟友」），由每章提交的剧情状态变更驱动，
        这里只读。它与角色档案里的「关系 relationships」自由字段是两回事。
      </InfoBanner>

      {rows.length === 0 ? (
        <EmptyState
          title="还没有人物关系"
          hint="人物关系会在章节提交（observer 节点）时被写入状态变更——写到第 2 章之后通常就有内容了。"
        />
      ) : visible.length === 0 ? (
        <EmptyState title="该角色暂无关系" hint="换一个角色，或看「全部角色」。" />
      ) : (
        <table className="table" data-testid="relationship-table">
          <thead>
            <tr>
              <th>从</th>
              <th>关系</th>
              <th>到</th>
              <th>状态版本</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.id} data-testid={`relationship-row-${r.id}`}>
                <td>{nameOf(r.from_character_id)}</td>
                <td data-testid={`relationship-type-${r.id}`}>{relationLabel(r.relation_type)}</td>
                <td>{nameOf(r.to_character_id)}</td>
                <td>{r.last_state_version}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
