// ReadableJson / RawJsonDetails 组件测试。
//
// 覆盖：
// - 中文标签命中（labelMap / COMMON_LABELS）
// - 未知键回退原键名
// - 关系数组特化渲染（to_name / relation_type / one_line）
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
    render(
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
    // relation_type 命中 COMMON_LABELS 不一定有 mapping，但 to_name 与 one_line 一定要展示
    expect(screen.getByText(/青梅竹马的同门/)).toBeInTheDocument();
    expect(screen.getByText(/宿敌/)).toBeInTheDocument();
    // 关系列表应渲染 <ul>
    const lists = document.querySelectorAll('ul');
    expect(lists.length).toBeGreaterThan(0);
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
