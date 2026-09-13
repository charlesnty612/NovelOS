// project-init 表单共享小组件（V4.0 前端模块化批次从 ProjectInitPanel.tsx 抽出，
// 样式/DOM 与拆分前逐字一致）。
import type { ChangeEvent, ReactNode } from 'react';

/** 「中文 label + 可选 hint」的表单小节外壳（idle 表单通用）。 */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div style={{ marginTop: 6 }}>
      <div className="muted small">
        {label}
        {hint ? <span className="muted small"> · {hint}</span> : null}
      </div>
      {children}
    </div>
  );
}

/**
 * 勾选行：label（flex / gap 6 / marginTop 6）+ checkbox + 说明小字。
 * 复用者：分步审阅开关、覆盖确认勾选。
 */
export function CheckboxRow({
  testid,
  checkboxTestid,
  checked,
  disabled,
  onChange,
  children,
}: {
  testid: string;
  checkboxTestid: string;
  checked: boolean;
  disabled: boolean;
  onChange: (e: ChangeEvent<HTMLInputElement>) => void;
  children: ReactNode;
}) {
  return (
    <label
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        marginTop: 6,
      }}
      data-testid={testid}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        data-testid={checkboxTestid}
      />
      <span className="small">{children}</span>
    </label>
  );
}
