import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { AILaunchSettings } from '../../lib/api';
import './local-ai-startup.css';

type NumericField = 'port' | 'context_size' | 'gpu_layers' | 'threads' | 'parallel' | 'image_max_tokens' | 'max_output_tokens';
export type LaunchDraft = Omit<AILaunchSettings, NumericField> & Record<NumericField, string>;
const fields: { key: NumericField; label: string; min: number; max: number }[] = [
  { key: 'context_size', label: '上下文长度', min: 512, max: 131072 },
  { key: 'gpu_layers', label: '显卡使用层数', min: -1, max: 999 },
  { key: 'threads', label: 'CPU 线程数', min: 0, max: 256 },
  { key: 'parallel', label: '同时处理数', min: 1, max: 4 },
  { key: 'image_max_tokens', label: '每张图像上限', min: 128, max: 4096 },
  { key: 'max_output_tokens', label: '单次回复上限', min: 128, max: 32768 },
];

export function toLaunchDraft(value: AILaunchSettings): LaunchDraft {
  return { ...value, show_console: value.show_console ?? true, port: String(value.port), context_size: String(value.context_size),
    gpu_layers: String(value.gpu_layers), threads: String(value.threads), parallel: String(value.parallel),
    image_max_tokens: String(value.image_max_tokens), max_output_tokens: String(value.max_output_tokens) };
}
export function launchPayload(value: LaunchDraft): AILaunchSettings {
  return { ...value, port: Number(value.port), context_size: Number(value.context_size),
    gpu_layers: Number(value.gpu_layers), threads: Number(value.threads), parallel: Number(value.parallel),
    image_max_tokens: Number(value.image_max_tokens), max_output_tokens: Number(value.max_output_tokens) };
}
export function launchError(value: LaunchDraft): string {
  for (const field of [{ key: 'port' as const, label: '服务端口', min: 1024, max: 65535 }, ...fields]) {
    const number = Number(value[field.key]);
    if (!/^-?\d+$/.test(value[field.key]) || !Number.isSafeInteger(number) || number < field.min || number > field.max) return `${field.label}需要 ${field.min} 到 ${field.max} 之间的整数。`;
  }
  if (!value.model_path.trim() || !value.server_path.trim()) return '请填写模型文件和运行程序路径。';
  if (Number(value.max_output_tokens) > Number(value.context_size)) return '单次回复上限不能大于上下文长度。';
  return '';
}

type Report = { ok: boolean; checks: { field: string; label: string; ok: boolean; message: string }[]; warnings: string[]; command: string };
type Props = { value: LaunchDraft; defaults: AILaunchSettings; model: string; command: string;
  disabled: boolean; onChange: (value: LaunchDraft) => void; onPicking: (busy: boolean) => void };

export default function LocalAIStartup({ value, defaults, model, command, disabled, onChange, onPicking }: Props) {
  const [report, setReport] = useState<Report | null>(null);
  const [status, setStatus] = useState('');
  const [checking, setChecking] = useState(false);
  const [picking, setPicking] = useState<string | null>(null);
  const picker = useRef<AbortController | null>(null);
  const [pickStatus, setPickStatus] = useState('');
  useEffect(() => () => picker.current?.abort(), []);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    pending.current?.abort(); pending.current = null;
    setChecking(false); setReport(null); setStatus('');
  }, [value, model]);
  useEffect(() => () => pending.current?.abort(), []);
  const error = launchError(value);
  const update = (patch: Partial<LaunchDraft>) => onChange({ ...value, ...patch });
  async function check() {
    const controller = new AbortController(); pending.current = controller;
    setChecking(true); setPickStatus(''); setStatus('正在检查本机文件与启动参数…'); setReport(null);
    try {
      const result = await request<Report>('/ai/startup/check', controller.signal, { startup: launchPayload(value), model });
      if (!controller.signal.aborted) { setReport(result); setStatus(result.ok ? '文件与参数检查通过；尚未启动模型。' : '有文件尚未就绪，请按下面的提示调整。'); }
    } catch (reason) { if (!controller.signal.aborted) setStatus(reason instanceof Error ? reason.message : '无法检查启动配置。'); }
    finally { if (pending.current === controller) { pending.current = null; setChecking(false); } }
  }
  async function pick(field: 'model_path' | 'vision_path' | 'server_path') {
    if (picker.current) return;
    const controller = new AbortController(); picker.current = controller;
    setPicking(field); onPicking(true); setPickStatus('本机文件选择窗口已打开，请选择文件或取消。');
    try {
      const result = await request<{ path: string | null }>('/ai/files/pick', controller.signal, { field });
      if (controller.signal.aborted) return;
      if (result.path !== null) { update({ [field]: result.path }); setPickStatus('文件已选入。选择文件不会自动保存或启动模型。'); }
      else setPickStatus('已取消选择，原路径没有改变。');
    } catch (reason) { if (!controller.signal.aborted) setPickStatus(reason instanceof Error ? reason.message : '无法选择文件，也可直接填写路径。'); }
    finally { if (picker.current === controller) { picker.current = null; setPicking(null); onPicking(false); } }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(command); setStatus('已复制。可在 PowerShell 中粘贴，或直接双击项目里的 start-ai.cmd。'); }
    catch { setStatus('浏览器不允许复制，请手动选中下方命令；也可双击 start-ai.cmd。'); }
  }
  return <fieldset className="local-ai-startup" disabled={disabled || checking}>
    <legend>本地模型启动配置</legend>
    <label htmlFor="ai-launch-model">模型文件路径</label>
    <div className="local-ai-path-row"><input id="ai-launch-model" value={value.model_path} maxLength={2048} spellCheck={false} onChange={event => update({ model_path: event.target.value })} /><button type="button" onClick={() => { void pick('model_path'); }} aria-busy={picking === 'model_path'}>选择模型文件</button></div>
    <label htmlFor="ai-launch-vision">视觉文件路径（看图时需要）</label>
    <div className="local-ai-path-row"><input id="ai-launch-vision" value={value.vision_path} maxLength={2048} spellCheck={false} onChange={event => update({ vision_path: event.target.value })} /><button type="button" onClick={() => { void pick('vision_path'); }} aria-busy={picking === 'vision_path'}>选择视觉文件</button></div>
    <label htmlFor="ai-launch-port">服务端口</label>
    <input id="ai-launch-port" type="number" min={1024} max={65535} value={value.port} onChange={event => update({ port: event.target.value })} />
    <label className="settings-check"><input type="checkbox" role="switch" checked={value.show_console} onChange={event => update({ show_console: event.target.checked })} />启动时显示命令行窗口</label>
    {pickStatus && <p className="settings-hint" role="status" aria-live="polite">{pickStatus}</p>}
    <details className="local-ai-advanced">
      <summary>高级启动参数（通常不用改）</summary>
      <div className="local-ai-fields">
        <label htmlFor="ai-launch-server">运行程序路径</label>
        <div className="local-ai-path-row"><input id="ai-launch-server" value={value.server_path} maxLength={2048} spellCheck={false} onChange={event => update({ server_path: event.target.value })} /><button type="button" onClick={() => { void pick('server_path'); }} aria-busy={picking === 'server_path'}>选择运行程序</button></div>
        {fields.map(field => <div className="local-ai-field" key={field.key}>
          <label htmlFor={'ai-launch-' + field.key}>{field.label}</label>
          <input id={'ai-launch-' + field.key} type="number" min={field.min} max={field.max} value={value[field.key]} onChange={event => update({ [field.key]: event.target.value })} />
        </div>)}
        <label className="settings-check"><input type="checkbox" checked={value.flash_attention} onChange={event => update({ flash_attention: event.target.checked })} />启用 Flash Attention 显卡加速</label>
        <button type="button" onClick={() => onChange({ ...toLaunchDraft(defaults), model_path: value.model_path, vision_path: value.vision_path, server_path: value.server_path, port: value.port, show_console: value.show_console })}>恢复推荐性能参数</button>
      </div>
    </details>
    {command && <details className="local-ai-advanced"><summary>手动启动命令（备用）</summary>
    <label htmlFor="ai-launch-command">启动命令（自动生成，PowerShell）</label>
    <textarea id="ai-launch-command" className="local-ai-command" value={command} readOnly rows={2} spellCheck={false} />
      <button type="button" onClick={() => { void copy(); }}>复制备用启动命令</button>
    </details>}
    <div className="settings-actions">
      <button type="button" disabled={!!error || !model.trim()} aria-busy={checking} onClick={() => { void check(); }}>{checking ? '检查中…' : '检查启动配置（不启动）'}</button>
    </div>
    {error && <p className="error-text" role="alert">{error}</p>}
    {status && <p className="settings-hint" role="status" aria-live="polite">{status}</p>}
    {report && <>
      <ul className="local-ai-checks">{report.checks.map(item => <li key={item.field}>{item.ok ? '✓' : '需调整'} {item.label}：{item.message}</li>)}</ul>
      {report.warnings.map(warning => <p className="settings-hint" key={warning}>{warning}</p>)}
      <details className="local-ai-advanced"><summary>查看实际模型参数（不含访问令牌）</summary>
        <pre className="local-ai-preview">{report.command}</pre>
      </details>
    </>}
  </fieldset>;
}
