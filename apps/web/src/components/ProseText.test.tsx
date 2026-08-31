import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProseText } from './ProseText';

describe('ProseText', () => {
  it('按空行分段并保留段内单换行', () => {
    render(<ProseText text={'第一段\n续行。\n\n第二段'} />);

    const paragraphs = document.querySelectorAll('.prose-block__p');
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]).toHaveTextContent('第一段 续行。');
    expect(paragraphs[0].textContent).toContain('\n');
    expect(screen.getByText('第二段')).toBeInTheDocument();
  });

  it('空串不渲染内容', () => {
    const { container } = render(<ProseText text="   " />);
    expect(container).toBeEmptyDOMElement();
  });

  it('重复段落+连续空行不告警、不多渲染空段', () => {
    // 场景：同一段重复出现 + 段落之间塞了多个连续空行。
    // 期望：仅渲染非空段；不出现 key 冲突告警；空段不进入 DOM。
    const text = '第一段\n\n\n\n第一段\n\n\n\n第二段\n\n';
    const { container } = render(<ProseText text={text} />);

    const paragraphs = container.querySelectorAll('.prose-block__p');
    // 期望：3 个非空段（第一段、第一段、第二段），空段被过滤。
    expect(paragraphs).toHaveLength(3);
    expect(paragraphs[0]).toHaveTextContent('第一段');
    expect(paragraphs[1]).toHaveTextContent('第一段');
    expect(paragraphs[2]).toHaveTextContent('第二段');
    // 没有空白文本节点冒充段（trim 后空串应被过滤）。
    paragraphs.forEach((p) => {
      expect(p.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    });
  });
});
