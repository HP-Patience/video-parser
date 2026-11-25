import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import { formatMegabytes as bytes } from '../../lib/format';

type Preview = { plan_id: string; size_bytes: number; note: string; protected: string[]; usage: Record<string, number | null>; entries: { id: string; kind: string; size_bytes: number; age_hours: number }[] };

// Moved out of the task list: cache cleanup is maintenance, not a task action.
export default function CacheManager({ activeCount }: { activeCount: number | null }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  async function guarded(work: (signal: AbortSignal) => Promise<void>, fallback: string) {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setNotice('');
    try { await work(controller.signal); }
    catch (reason) { if (!controller.signal.aborted) setNotice(reason instanceof Error ? reason.message : fallback); }
    finally { if (!controller.signal.aborted) { active.current = null; setBusy(false); } }
  }
  const loadPreview = () => guarded(async signal => {
    const result = await request<Preview>('/cache/preview', signal, {});
    if (!signal.aborted) setPreview(result);
  }, '预览失败');
  const clean = () => guarded(async signal => {
    if (!preview) return;
    const result = await request<{ size_bytes: number; skipped: string[] }>('/cache/clean', signal, { plan_id: preview.plan_id, confirm: true });
    if (signal.aborted) return;
    setPreview(null);
    setNotice(`清理完成：${bytes(result.size_bytes)}，跳过 ${result.skipped.length} 项变化或不可清理的缓存。`);
  }, '清理失败');
  const running = activeCount ?? 0;
  return <section aria-labelledby="settings-cache">
    <div className="cache-heading">
      <h3 id="settings-cache">临时缓存</h3>
      {!preview && <button type="button" disabled={busy} aria-busy={busy} onClick={() => { void loadPreview(); }}>缓存管理</button>}
    </div>
    {preview && !preview.entries.length && <p className="settings-hint">暂无可清理的临时缓存。<button type="button" className="link-button" onClick={() => setPreview(null)}>知道了</button></p>}
    {preview && !!preview.entries.length && <div className="cache-preview"><h4>清理预览</h4><p>{preview.note}</p>
      <details className="cache-usage-details"><summary>查看缓存分类用量</summary><p>临时字幕副本：{bytes(preview.usage.subtitle_scratch || 0)} · 视频/分析临时副本：{bytes(preview.usage.workflow_scratch || 0)} · 可复用字幕缓存：{bytes(preview.usage.subtitle_reuse_cache || 0)}</p></details>
      <p>不会删除：{preview.protected.join('、')}。</p>
      <ul>{preview.entries.map(item => <li key={item.id}>{item.kind} · {bytes(item.size_bytes)} · {item.age_hours} 小时前<details className="cache-item-id"><summary>缓存 ID</summary><code>{item.id}</code></details></li>)}</ul>
      <p>可清理 {preview.entries.length} 项，共 {bytes(preview.size_bytes)}。确认有效期 10 分钟。</p>
      {running > 0 && <p className="settings-hint">有 {running} 项任务进行中，完成后才能清理。</p>}
      <div className="cache-actions"><button type="button" className="danger-button" disabled={busy || running > 0 || !preview.entries.length} aria-busy={busy} onClick={() => { void clean(); }}>清理临时缓存</button><button type="button" disabled={busy} onClick={() => setPreview(null)}>不清理</button></div>
    </div>}
    {notice && <p role="status" aria-live="polite">{notice}</p>}
  </section>;
}
