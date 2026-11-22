import { useRef, type CSSProperties, type KeyboardEvent } from 'react';
import './choice-controls.css';

/* 第 020 期：选择控件。这里只解决三个实际问题：
   一眼看出当前值、点一下就能改、键盘能到得了。
   - Switch 用于"开 / 关"这种二值状态，替代裸复选框；
   - Segmented 用于"从少量互斥选项里选一个"，替代下拉框与一排 aria-pressed 按钮。

   Switch 底层仍是 <input type="checkbox">，所以 label 关联、表单提交和读屏语义都不用重写；
   只是把外观换成轨道 + 滑块，并补 role="switch"，让读屏念"开 / 关"而不是"勾选"。 */

export type SwitchProps = {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  /** 可选的一行说明，放在标签下方，用来解释打开之后会发生什么。 */
  hint?: string;
  /** 需要单独的读屏名字时用（例如同时显示"允许调用"与工具名）。 */
  ariaLabel?: string;
  className?: string;
};

export function Switch({ checked, onChange, label, disabled = false, hint, ariaLabel, className = '' }: SwitchProps) {
  return <label className={`vp-switch${disabled ? ' is-disabled' : ''}${className ? ' ' + className : ''}`}>
    <input type="checkbox" role="switch" checked={checked} disabled={disabled} aria-label={ariaLabel}
      onChange={event => onChange(event.target.checked)} />
    <span className="vp-switch-track" aria-hidden="true"><span className="vp-switch-thumb" /></span>
    <span className="vp-switch-text">
      <span className="vp-switch-label">{label}</span>
      {hint && <span className="vp-switch-hint">{hint}</span>}
    </span>
  </label>;
}

export type SegmentedOption<T extends string | number> = { value: T; label: string; disabled?: boolean };

export type SegmentedProps<T extends string | number> = {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (next: T) => void;
  /** 整组的名字，读屏会念"常用速度，单选组"。 */
  label: string;
  disabled?: boolean;
  /** 选项多于 4 个时按列折行（如 6 档速度排 3 列 2 行），滑块退化为逐格高亮。 */
  columns?: number;
  className?: string;
};

/* 单选组用 radiogroup + radio，而不是一排 aria-pressed 按钮：
   "同时只有一个成立"本来就是单选语义，键盘也就该按方向键在选项间移动。 */
export function Segmented<T extends string | number>({
  value, options, onChange, label, disabled = false, columns, className = '',
}: SegmentedProps<T>) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const active = options.findIndex(option => option.value === value);
  const wrapped = !!columns && columns < options.length;
  // 当前值不在选项里（比如手输了 1.15×）时不能假装选中第一格：滑块藏起来，全部留空。
  const none = active < 0;
  const focusable = none ? Math.max(0, options.findIndex(option => !option.disabled)) : active;

  function step(from: number, delta: number) {
    const total = options.length;
    for (let i = 1; i <= total; i++) {
      const next = (from + delta * i + total * i) % total;
      if (!options[next].disabled) return next;
    }
    return from;
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (disabled) return;
    const delta = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1
      : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const next = step(focusable, delta);
    if (next === focusable && options[next].value === value) return;
    onChange(options[next].value);
    // 选中与聚焦一起走，否则方向键会把焦点留在原来那一格。
    buttons.current[next]?.focus();
  }

  const style = {
    '--vp-seg-count': options.length,
    '--vp-seg-index': Math.max(0, active),
    ...(columns ? { '--vp-seg-cols': columns } : {}),
  } as CSSProperties;

  return <div className={['vp-segmented', wrapped ? 'is-grid' : '', none ? 'has-none' : '',
    disabled ? 'is-disabled' : '', className].filter(Boolean).join(' ')}
    role="radiogroup" aria-label={label} onKeyDown={onKeyDown} style={style}>
    {!wrapped && <span className="vp-segmented-thumb" aria-hidden="true" />}
    {options.map((option, index) => <button key={String(option.value)} ref={node => { buttons.current[index] = node; }}
      type="button" className="vp-seg" role="radio" aria-checked={option.value === value} disabled={disabled || option.disabled}
      tabIndex={index === focusable ? 0 : -1}
      onClick={() => { if (option.value !== value) onChange(option.value); }}>{option.label}</button>)}
  </div>;
}
