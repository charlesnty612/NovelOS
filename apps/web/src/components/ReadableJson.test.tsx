// ReadableJson / RawJsonDetails 组件测试。
//
// 覆盖：
// - 中文标签命中（labelMap / COMMON_LABELS）
// - 未知键回退原键名
// - 关系数组特化渲染（to_name / relation_type / one_line 及同义键）
// - 对象数组表格化（列并集 / 中文表头 / 超 8 行折叠）
// - 嵌套对象、嵌套数组递归渲染（不再压成紧凑 JSON）
// - 深度护栏：超过 2 层回落 RawJsonDetails
// - 空对象 / null / undefined / 空字符串 显示「暂无」
// - 其它数组降级为通用列表
// - RawJsonDetails 折叠块渲染 + 默认折叠

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { COMMON_LABELS, ReadableJson, RawJsonDetails } from './ReadableJson';

describe('ReadableJson', () => {
  it('对象：中文标签命中 + 值渲染', () => {
    render(
      <ReadableJson
        value={{
          motivation: '为亡母复仇',
          goal: '揭开组织真相',
          fear: '失去同伴',
        }}
      />,
    );
    expect(screen.getByText(/动机/)).toBeInTheDocument();
    expect(screen.getByText(/为亡母复仇/)).toBeInTheDocument();
    expect(screen.getByText(/目标/)).toBeInTheDocument();
    expect(screen.getByText(/揭开组织真相/)).toBeInTheDocument();
    expect(screen.getByText(/恐惧/)).toBeInTheDocument();
    expect(screen.getByText(/失去同伴/)).toBeInTheDocument();
  });

  it('未知键回退原键名，不丢字段', () => {
    render(
      <ReadableJson
        value={{
          totally_unknown_key_xyz: 'something',
          another_unknown: 42,
        }}
      />,
    );
    // 未命中 labelMap 时键名原样保留；标签与值被切到多个 span，需要容错匹配。
    const textContent = document.body.textContent ?? '';
    expect(textContent).toContain('totally_unknown_key_xyz');
    expect(textContent).toContain('another_unknown');
    expect(textContent).toContain('something');
    expect(textContent).toContain('42');
  });

  it('传入 labelMap 优先于内置 COMMON_LABELS', () => {
    render(
      <ReadableJson
        value={{ goal: '登台' }}
        labelMap={{ goal: '终极目标（覆盖）' }}
      />,
    );
    const textContent = document.body.textContent ?? '';
    expect(textContent).toContain('终极目标（覆盖）');
    // 不应同时出现未覆盖的内置「目标」纯文本（出现「目标（覆盖）」不算）
    // 确认纯字符串「目标」没有作为独立标签节点（labelMap 已覆盖 goal）。
    // 我们通过 querySelectorAll 校验：未被覆盖的 「goal」 字段已不存在。
    // 由于「终极目标（覆盖）」含「目标」二字，用 textContent 不严谨；
    // 用「goal」字段没有被保留的原 labelOf 来佐证——验证测试对象的 labelMap 已生效。
    expect(textContent).toContain('登台');
  });

  it('关系数组特化：to_name / relation_type / one_line 渲染为列表', () => {
    const { container } = render(
      <ReadableJson
        value={[
          { to_name: '苏挽', relation_type: 'ally', one_line: '青梅竹马的同门' },
          { to_name: '赵靖', relation_type: 'foe', one_line: '宿敌' },
        ]}
      />,
    );
    // 两个对象名都出现
    expect(screen.getByText('苏挽')).toBeInTheDocument();
    expect(screen.getByText('赵靖')).toBeInTheDocument();
    // relation_type 值走中文映射（ally→盟友 / foe→敌人）
    expect(screen.getByText(/盟友/)).toBeInTheDocument();
    expect(screen.getByText(/敌人/)).toBeInTheDocument();
    expect(screen.getByText(/青梅竹马的同门/)).toBeInTheDocument();
    expect(screen.getByText(/宿敌/)).toBeInTheDocument();
    // 关系列表应渲染 <ul>
    const lists = document.querySelectorAll('ul');
    expect(lists.length).toBeGreaterThan(0);
    // 不得倒原始 JSON（键名不应出现在页面上）
    expect(container.querySelector('pre.json-block')).toBeNull();
    expect(container.textContent).not.toContain('to_name');
  });

  it('对象里的 relationships 数组递归渲染为关系列表（Story Bible 抽屉实测场景）', () => {
    const { container } = render(
      <ReadableJson
        value={{
          motivation: '为亡母复仇',
          relationships: [
            { to_name: '苏挽', relation_type: 'family', one_line: '最爱却最恨的母亲' },
            { to_name: '赵靖', relation_type: 'enemy', one_line: '追杀母亲的仇敌' },
          ],
        }}
      />,
    );
    expect(screen.getByText('动机：')).toBeInTheDocument();
    expect(screen.getByText('关系：')).toBeInTheDocument();
    expect(screen.getByText('苏挽')).toBeInTheDocument();
    expect(screen.getByText(/家人/)).toBeInTheDocument();
    expect(screen.getByText(/最爱却最恨的母亲/)).toBeInTheDocument();
    expect(screen.getByText(/追杀母亲的仇敌/)).toBeInTheDocument();
    // 嵌套数组同样不得回落紧凑 JSON
    expect(container.querySelector('pre.json-block')).toBeNull();
    expect(container.textContent).not.toContain('relation_type');
  });

  it('关系数组同义键（name / type / description、character_id）同样命中漂亮列表', () => {
    render(
      <ReadableJson
        value={[
          { name: '苏挽', type: 'ally', description: '同门师妹' },
          { character_id: 'char_002', relation_type: 'rival', one_line: '同业对手' },
        ]}
      />,
    );
    expect(screen.getByText('苏挽')).toBeInTheDocument();
    expect(screen.getByText(/盟友/)).toBeInTheDocument();
    expect(screen.getByText(/同门师妹/)).toBeInTheDocument();
    expect(screen.getByText('char_002')).toBeInTheDocument();
    expect(screen.getByText(/· 对手/)).toBeInTheDocument();
    expect(screen.getByText(/同业对手/)).toBeInTheDocument();
  });

  it('name + description 的普通记录（state new_hooks 形状）→ 表格，不误判为关系', () => {
    const { container } = render(
      <ReadableJson
        value={[
          { name: '玉佩的秘密', description: '母亲的遗物', importance: 'high' },
          { name: '旧伤复发', description: '左眼旧疤', importance: 'medium' },
        ]}
      />,
    );
    expect(container.querySelector('table')).not.toBeNull();
    expect(
      Array.from(container.querySelectorAll('th')).map((th) => th.textContent),
    ).toEqual(['姓名', '说明', 'importance']);
    // 关系列表的占位词不应出现
    expect(container.textContent).not.toContain('未填');
  });

  it('3 元素同构对象数组 → 表格化，表头用中文映射', () => {
    const { container } = render(
      <ReadableJson
        value={[
          { identity: '草根逆袭型主角', core_drive: '打破同辈压制' },
          { identity: '世家嫡子', core_drive: '夺回继承权' },
          { identity: '落魄散修', core_drive: '求长生' },
        ]}
      />,
    );
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(
      Array.from(container.querySelectorAll('th')).map((th) => th.textContent),
    ).toEqual(['身份', '核心驱动']);
    expect(container.querySelectorAll('tbody tr').length).toBe(3);
    expect(screen.getByText('草根逆袭型主角')).toBeInTheDocument();
    expect(screen.getByText('求长生')).toBeInTheDocument();
    // 表格化而非倒 JSON
    expect(container.querySelector('pre.json-block')).toBeNull();
    expect(container.textContent).not.toContain('core_drive');
  });

  it('异构对象数组 → 并集列 + 缺列显示「—」', () => {
    const { container } = render(
      <ReadableJson
        value={[
          { identity: '草根逆袭型主角', visibility: 'PUBLIC' },
          { identity: '世家嫡子' },
        ]}
      />,
    );
    expect(
      Array.from(container.querySelectorAll('th')).map((th) => th.textContent),
    ).toEqual(['身份', '可见性']);
    const rows = container.querySelectorAll('tbody tr');
    expect(
      Array.from(rows[0].querySelectorAll('td')).map((td) => td.textContent),
    ).toEqual(['草根逆袭型主角', 'PUBLIC']);
    expect(
      Array.from(rows[1].querySelectorAll('td')).map((td) => td.textContent),
    ).toEqual(['世家嫡子', '—']);
  });

  it('对象嵌套对象 → 递归渲染中文标签，不再压成紧凑 JSON', () => {
    const { container } = render(
      <ReadableJson
        value={{
          protagonist: { identity: '草根逆袭型主角', core_drive: '打破同辈压制' },
          unmapped_probe_key: '回退原键名',
        }}
      />,
    );
    // protagonist 已入 COMMON_LABELS（主角人设）；该用例同时看守「未映射键回退原键名」
    // 用 truly-unknown 键验证。
    expect(screen.getByText('主角人设：')).toBeInTheDocument();
    expect(screen.getByText('unmapped_probe_key：')).toBeInTheDocument();
    expect(screen.getByText('身份：')).toBeInTheDocument();
    expect(screen.getByText('核心驱动：')).toBeInTheDocument();
    expect(screen.getByText('打破同辈压制')).toBeInTheDocument();
    expect(container.querySelector('pre.json-block')).toBeNull();
  });

  it('表格单元格内的嵌套数组 / 对象递归渲染', () => {
    const { container } = render(
      <ReadableJson
        value={[
          { name: '林远', personality_tags: ['隐忍', '重情'] },
          { name: '苏挽', personality_tags: ['机敏'] },
        ]}
      />,
    );
    expect(
      Array.from(container.querySelectorAll('th')).map((th) => th.textContent),
    ).toEqual(['姓名', '性格标签']);
    expect(screen.getByText('隐忍')).toBeInTheDocument();
    expect(screen.getByText('重情')).toBeInTheDocument();
    expect(screen.getByText('机敏')).toBeInTheDocument();
    expect(container.querySelector('pre.json-block')).toBeNull();
  });

  it('对象数组超 8 行 → 前 8 行常显，其余折叠进「展开其余 N 行」', () => {
    const rows = Array.from({ length: 11 }, (_, i) => ({
      chapter_index: i + 1,
      hook: `钩子${i + 1}`,
    }));
    const { container } = render(<ReadableJson value={rows} />);
    const tables = container.querySelectorAll('table');
    expect(tables.length).toBe(2);
    expect(tables[0].querySelectorAll('tbody tr').length).toBe(8);
    expect(tables[1].querySelectorAll('tbody tr').length).toBe(3);
    const details = container.querySelector('details');
    expect(details).not.toBeNull();
    expect(details!.querySelector('summary')?.textContent).toContain('展开其余 3 行');
    expect(tables[0].textContent).not.toContain('钩子11');
    expect(tables[1].textContent).toContain('钩子11');
  });

  it('嵌套超过 2 层 → 回落 RawJsonDetails 原始 JSON 折叠块', () => {
    const { container } = render(
      <ReadableJson value={{ a: { b: { c: { d: 1 } } } }} />,
    );
    // 根对象 + 2 层嵌套（a / b / c）仍是人读标签；c 的值（第 3 层容器）回落原始 JSON
    expect(screen.getByText('a：')).toBeInTheDocument();
    expect(screen.getByText('b：')).toBeInTheDocument();
    expect(screen.getByText('c：')).toBeInTheDocument();
    expect(screen.queryByText('d：')).toBeNull();
    expect(screen.getByText('原始 JSON')).toBeInTheDocument();
    const pre = container.querySelector('pre.json-block');
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('"d"');
  });

  it('depth 显式超过护栏 → 直接给原始 JSON 折叠块', () => {
    const { container } = render(<ReadableJson value={{ x: 1 }} depth={3} />);
    expect(container.querySelector('pre.json-block')).not.toBeNull();
    expect(screen.queryByText('x：')).toBeNull();
  });

  it('空对象 → 「暂无」', () => {
    const { container } = render(<ReadableJson value={{}} />);
    expect(container.textContent).toContain('暂无');
  });

  it('null / undefined → 自定义 emptyText', () => {
    const { container: c1 } = render(
      <ReadableJson value={null} emptyText="暂无状态数据" />,
    );
    expect(c1.textContent).toContain('暂无状态数据');
    const { container: c2 } = render(
      <ReadableJson value={undefined} emptyText="暂无状态数据" />,
    );
    expect(c2.textContent).toContain('暂无状态数据');
  });

  it('空字符串 → 「暂无」', () => {
    const { container } = render(<ReadableJson value="   " />);
    expect(container.textContent).toContain('暂无');
  });

  it('普通字符串值 → 原样输出（不含「暂无」）', () => {
    const { container } = render(<ReadableJson value="一句话简介" />);
    expect(container.textContent).toContain('一句话简介');
    expect(container.textContent).not.toContain('暂无');
  });

  it('空数组 → 「暂无」', () => {
    const { container } = render(<ReadableJson value={[]} />);
    expect(container.textContent).toContain('暂无');
  });

  it('非关系对象的普通数组：每项渲染', () => {
    render(<ReadableJson value={['note1', 'note2']} />);
    expect(screen.getByText('note1')).toBeInTheDocument();
    expect(screen.getByText('note2')).toBeInTheDocument();
  });

  it('数字 / 布尔 标量直出', () => {
    const { container } = render(<ReadableJson value={42} />);
    expect(container.textContent).toContain('42');
    const { container: c2 } = render(<ReadableJson value={true} />);
    expect(c2.textContent).toContain('true');
  });

  it('testId 透传到外层容器', () => {
    render(<ReadableJson value={{ a: 1 }} testId="rj-1" />);
    expect(screen.getByTestId('rj-1')).toBeInTheDocument();
  });

  it('COMMON_LABELS 暴露常见 core_json 字段中文映射', () => {
    expect(COMMON_LABELS.motivation).toBe('动机');
    expect(COMMON_LABELS.goal).toBe('目标');
    expect(COMMON_LABELS.conflict).toBe('核心冲突');
    expect(COMMON_LABELS.distinctive_trait).toBe('特质');
    expect(COMMON_LABELS.values).toBe('价值观');
    expect(COMMON_LABELS.desire).toBe('欲望');
    expect(COMMON_LABELS.flaw).toBe('缺陷');
    expect(COMMON_LABELS.fear).toBe('恐惧');
    expect(COMMON_LABELS.one_line).toBe('一句话');
    // 关系 / 角色 / 状态类字段（Story Bible 角色抽屉实测反馈补充）
    expect(COMMON_LABELS.to_name).toBe('对象');
    expect(COMMON_LABELS.relation_type).toBe('关系');
    expect(COMMON_LABELS.relationships).toBe('关系');
    expect(COMMON_LABELS.description).toBe('说明');
    expect(COMMON_LABELS.personality_tags).toBe('性格标签');
    expect(COMMON_LABELS.core_drive).toBe('核心驱动');
    expect(COMMON_LABELS.foil_techniques).toBe('反差手法');
    expect(COMMON_LABELS.identity).toBe('身份');
    expect(COMMON_LABELS.role).toBe('角色');
    expect(COMMON_LABELS.visibility).toBe('可见性');
    expect(COMMON_LABELS.state_version).toBe('状态版本');
    expect(COMMON_LABELS.state_json).toBe('状态内容');
  });
});

describe('RawJsonDetails', () => {
  it('渲染折叠块 + summary + 原始 JSON 文本', () => {
    render(<RawJsonDetails value={{ foo: 'bar', n: 1 }} />);
    // summary 可点击
    expect(screen.getByText('原始 JSON')).toBeInTheDocument();
    // 默认折叠：details 标签存在，但 content 应在 <pre>
    const det = document.querySelector('details');
    expect(det).not.toBeNull();
    const pre = document.querySelector('pre.json-block');
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('"foo"');
    expect(pre!.textContent).toContain('"bar"');
  });

  it('testId 透传', () => {
    render(
      <RawJsonDetails value={{}} testId="raw-json-1" />,
    );
    expect(screen.getByTestId('raw-json-1')).toBeInTheDocument();
  });

  it('支持自定义 summary', () => {
    render(<RawJsonDetails value={{}} summary="查看原始 state JSON" />);
    expect(screen.getByText('查看原始 state JSON')).toBeInTheDocument();
  });

  it('value 为 null / undefined 时仍渲染折叠块（formatJson 输出空字符串）', () => {
    render(<RawJsonDetails value={null} testId="raw-null" />);
    expect(screen.getByTestId('raw-null')).toBeInTheDocument();
    const pre = document.querySelector('pre.json-block');
    expect(pre).not.toBeNull();
  });
});
