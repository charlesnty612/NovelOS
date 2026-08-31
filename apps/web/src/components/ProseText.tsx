interface Props {
  text: string;
}

export function ProseText({ text }: Props) {
  if (!text.trim()) return null;

  // 用 index 作 key（同一段重复时仍稳定且不冲突），
  // 并过滤空段，避免连续 \n\n\n\n 渲染悬空缩进空 p。
  const paragraphs = text
    .split('\n\n')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  return (
    <>
      {paragraphs.map((paragraph, index) => (
        <p className="prose-block__p" key={index}>
          {paragraph}
        </p>
      ))}
    </>
  );
}
