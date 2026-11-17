import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { AISettings, AIRuntimeStatus } from '../../lib/api';

const labels = { unconfigured: '待保存配置', stopped: '未启动', starting: '加载中', running: '已就绪', failed: '启动失败', occupied: '需检查现有服务' };
type Props = { saved: AISettings; visible: boolean; busy: boolean; invalid: boolean; dirty: boolean; providerId?: string;
  onStart: () => Promise<AIRuntimeStatus | null> };

export default function LocalAIRuntime({ saved, visible, busy, invalid, dirty, providerId, onStart }: Props) {
  const [runtime, setRuntime] = useState<AIRuntimeStatus | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const refresh = useRef<(() => Promise<void>) | null>(null);
  useEffect(() => {
    if (!visible || busy) return;
    const controller = new AbortController();
    let reading = false;
    const load = async () => {
      if (reading || controller.signal.aborted) return;
      reading = true; setRefreshing(true);
      try {
        const next = await request<AIRuntimeStatus>('/ai/runtime' + (providerId ? '?provider_id=' + encodeURIComponent(providerId) : ''), controller.signal);
        if (!controller.signal.aborted) { setRuntime(next); setError(''); }
      } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取模型运行状态。'); }
      finally { reading = false; if (!controller.signal.aborted) setRefreshing(false); }
    };
    refresh.current = load; void load();
    const timer = window.setInterval(() => { void load(); }, 2500);
    return () => { controller.abort(); window.clearInterval(timer); refresh.current = null; };
  }, [visible, busy, providerId, saved.provider, saved.base_url, saved.model, saved.startup]);
  async function start() {
    setError('');
    const next = await onStart();
    if (next) setRuntime(next);
  }
  const active = runtime?.state === 'starting' || runtime?.state === 'running';
  return <section className="ai-runtime-card" aria-labelledby="ai-runtime-title">
    <div className="ai-runtime-heading"><h3 id="ai-runtime-title">本地模型服务</h3><span className={'ai-runtime-state is-' + (runtime?.state || 'unknown')}>
      {runtime ? labels[runtime.state] : error ? '状态未知' : '读取状态…'}{runtime?.state === 'starting' && runtime.elapsed_seconds > 0 ? ` · ${runtime.elapsed_seconds} 秒` : ''}</span></div>
    {/* When the model is running the badge says it all; the sentence is for loading, problems and the stopped state. */}
    {(error || runtime?.state !== 'running') && <p className="settings-hint" role="status" aria-live="polite">{error || runtime?.message || '正在检查本机模型服务…'}</p>}
    <div className="settings-actions">
      {runtime?.state !== 'running' && <button type="button" className="primary" disabled={busy || invalid || active || runtime?.state === 'occupied'} aria-busy={busy || runtime?.state === 'starting'}
        title="启动会占用显存，加载可能需要几分钟；只启动本机 llama.cpp，不下载模型" onClick={() => { void start(); }}>
        {busy ? '正在提交…' : runtime?.state === 'starting' ? '正在加载模型…' : dirty ? '保存并启动模型' : '启动本地模型'}</button>}
      <button type="button" disabled={busy || refreshing} aria-busy={refreshing} onClick={() => { void refresh.current?.(); }}>刷新运行状态</button>
    </div>
    {dirty && active && <p className="settings-hint">当前模型仍在使用旧参数。保存不会自动重启；请先在模型窗口按 Ctrl+C 停止，再启动新配置。</p>}
  </section>;
}
