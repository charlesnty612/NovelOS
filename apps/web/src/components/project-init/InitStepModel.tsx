// project-init idle 表单「模型选择步」：本次初始化模型档案覆盖下拉
// （V4.0 前端模块化批次从 ProjectInitPanel.tsx 原样搬出）。
import type { ModelProfile } from '../../api/types';
import { Field } from './InitField';

export interface InitStepModelProps {
  /** 本次初始化模型档案：'' = 走全局 capability_bindings。 */
  initModelProfileId: string;
  setInitModelProfileId: (v: string) => void;
  /** 已启用的 model_profiles 列表（驱动下拉 options）。 */
  initProfiles: ModelProfile[];
  busy: boolean;
}

export function InitStepModel({
  initModelProfileId,
  setInitModelProfileId,
  initProfiles,
  busy,
}: InitStepModelProps) {
  return (
    <Field label="本次初始化模型" hint="覆盖全局绑定；留空走环节绑定（默认）">
      <select
        className="input"
        value={initModelProfileId}
        onChange={(e) => setInitModelProfileId(e.target.value)}
        disabled={busy}
        data-testid="project-init-model-profile"
      >
        <option value="">默认绑定（全局）</option>
        {initProfiles
          .filter((p) => Number(p.enabled) === 1)
          .map((p) => (
            <option key={p.profile_id} value={p.profile_id}>
              {p.name}（{p.provider}/{p.model}）
            </option>
          ))}
      </select>
    </Field>
  );
}
