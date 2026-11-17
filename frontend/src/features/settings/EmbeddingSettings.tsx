import { useEffect, useState } from 'react';
import { request } from '../../lib/api';
type Settings = { base_url: string; model: string; key_saved: boolean; configured: boolean };
export default function EmbeddingSettings() {
  const [saved, setSaved] = useState<Settings | null>(null);
  const [data, setData] = useState<Settings | null>(null);
  const [key, setKey] = useState(''), [confirmClear, setConfirmClear] = useState(false);
  const [message, setMessage] = useState(''), [failed, setFailed] = useState(false), [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const c = new AbortController();
    request<Settings>('/embedding/settings', c.signal).then(value => { setSaved(value); setData(value); }).catch(e => {
      if (!c.signal.aborted) { setFailed(true); setMessage(String(e.message)); }
    });
    return () => c.abort();
  }, []);
  const dirty = !!saved && !!data && (data.base_url !== saved.base_url || data.model !== saved.model || !!key);
  function update(patch: Partial<Settings>) { setData(old => old && { ...old, ...patch }); setMessage(''); setFailed(false); setConfirmClear(false); }
  async function save(clearKey = false) {
    if (!data || !saved) return;
    setBusy(true); setMessage(''); setFailed(false);
    try {
      const result = await request<Settings>('/embedding/settings', new AbortController().signal,
        { base_url: data.base_url, model: data.model, api_key: clearKey ? undefined : key || undefined, clear_key: clearKey });
      setSaved(result); setData(result); setKey(''); setConfirmClear(false);
      setMessage(clearKey ? '检索配置已保存，密钥已清除。' : 'Embedding 配置已保存；更换模型或地址后需重建索引。');
      window.dispatchEvent(new Event('video-parser:embedding-changed'));
    } catch (e) { setFailed(true); setMessage(e instanceof Error ? e.message : '保存失败'); }
    finally { setBusy(false); }
  }
  return <section className="model-source embedding-settings" aria-labelledby="embedding-settings-title">
    <div className="model-source-row"><div className="model-source-copy"><h3 id="embedding-settings-title">检索模型（Embedding）</h3>
      <span className="model-source-state" translate={saved?.configured ? 'no' : undefined}>{saved ? saved.configured ? saved.model : '未配置' : failed ? '状态未知' : '正在读取…'}</span></div>
      <button type="button" aria-label="配置检索模型" aria-expanded={open} aria-controls="embedding-source-config" onClick={() => setOpen(value => !value)}>{open ? '收起' : '配置'}</button></div>
    {dirty && <p className="settings-hint" role="status">有未保存的检索配置修改。</p>}
    {message && <p className={failed ? 'error-text' : 'settings-save-status'} role={failed ? 'alert' : 'status'} aria-live="polite">{message}</p>}
    <div className="model-source-config" id="embedding-source-config" hidden={!open}>
      {data && <>
        <div className="settings-form-fields">
          <div className="settings-field"><label htmlFor="embedding-url">接口地址</label>
            <input disabled={busy} id="embedding-url" aria-label="检索接口地址" type="url" value={data.base_url} placeholder="http://127.0.0.1:11434/v1" onChange={e => update({ base_url: e.target.value })} /></div>
          <div className="settings-field"><label htmlFor="embedding-model">模型 ID</label>
            <input disabled={busy} id="embedding-model" aria-label="检索模型 ID" type="text" value={data.model} onChange={e => update({ model: e.target.value })} /></div>
          <div className="settings-field"><label htmlFor="embedding-key">API Key</label>
            <input disabled={busy} id="embedding-key" aria-label="检索 API Key" type="password" value={key} autoComplete="off" placeholder={saved?.key_saved ? '已保存，留空保留' : '本机服务可留空'} onChange={e => { setKey(e.target.value); setMessage(''); setConfirmClear(false); }} /></div>
        </div>
        <div className="settings-actions"><button type="button" className="primary" disabled={busy || !dirty} onClick={() => { void save(); }}>保存检索配置</button>
          {saved?.key_saved && !confirmClear && <button type="button" disabled={busy} onClick={() => setConfirmClear(true)}>清除已保存的密钥</button>}</div>
        {confirmClear && <div className="embedding-clear-confirm" role="group" aria-label="确认清除检索密钥">
          <p className="settings-hint">清除检索密钥并保存当前检索配置？生成模型的密钥不受影响。</p>
          <div className="settings-actions"><button type="button" disabled={busy} onClick={() => { void save(true); }}>确认清除并保存</button><button type="button" disabled={busy} onClick={() => setConfirmClear(false)}>取消</button></div>
        </div>}
      </>}
    </div>
  </section>;
}
