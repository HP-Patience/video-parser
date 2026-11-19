import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';

type Status = { configured: boolean; cookies: number; browser: string | null; updated_at: number | null; source: 'file' | 'browser' | null };

const BROWSERS: [string, string][] = [['chrome', 'Chrome'], ['edge', 'Edge'], ['firefox', 'Firefox'], ['brave', 'Brave']];

// YouTube has no in-app login: the reader's own browser session is imported and read by yt-dlp.
export default function YoutubeLogin({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [pasted, setPasted] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    const controller = new AbortController();
    request<Status>('/youtube-auth/status', controller.signal)
      .then(setStatus)
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取 YouTube 登录态。'); });
    return () => { controller.abort(); if (element?.open) element.close(); };
  }, []);

  async function run(name: string, path: string, body: unknown) {
    setBusy(name); setError('');
    try {
      const next = await request<Status>(path, new AbortController().signal, body);
      setStatus(next);
      if (path === '/youtube-auth/cookies') setPasted('');
      onSuccess();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败，请重试。');
    } finally {
      setBusy('');
    }
  }

  return <dialog ref={dialog} className="settings-dialog login-dialog" aria-labelledby="youtube-login-title" onCancel={event => { event.preventDefault(); onClose(); }}>
    <h2 id="youtube-login-title">导入 YouTube 登录态</h2>
    <p className="login-status" role="status" aria-live="polite">
      {status === null ? '正在读取…'
        : status.configured
          ? `已配置：${status.source === 'browser' ? '跟随浏览器登录态' : '已导入 cookies 文件'}（${status.cookies} 条 cookie）`
          : '未配置：YouTube 视频会被要求登录校验。'}
    </p>
    <div className="youtube-sources">
      {BROWSERS.map(([value, label]) => <button type="button" key={value} disabled={Boolean(busy)}
        aria-busy={busy === value} onClick={() => { void run(value, '/youtube-auth/import', { browser: value }); }}>
        {busy === value ? `正在读取 ${label}…` : `从 ${label} 导入`}</button>)}
    </div>
    <p className="notice">Chrome / Edge 127 以后把 cookie 换成了系统级加密，直接读取可能失败；失败时请用浏览器扩展导出 cookies.txt 再粘贴到下面。</p>
    <label htmlFor="youtube-cookies">或粘贴 cookies.txt（Netscape 格式，也支持浏览器扩展导出的 JSON）</label>
    <textarea id="youtube-cookies" rows={5} spellCheck={false} value={pasted} disabled={Boolean(busy)}
      onChange={event => setPasted(event.target.value)} placeholder="# Netscape HTTP Cookie File…" />
    {error && <p className="error-text" role="alert">{error}</p>}
    <input ref={picker} type="file" accept=".txt,.json,text/plain,application/json" hidden
      onChange={event => {
        const picked = event.target.files?.[0];
        event.target.value = '';
        if (!picked) return;
        if (picked.size > 1024 * 1024) { setError('文件过大，请只导出 youtube.com 的 cookies。'); return; }
        setError('');
        void picked.text().then(text => setPasted(text));
      }} />
    <div className="login-actions">
      <button type="button" disabled={Boolean(busy)} onClick={() => picker.current?.click()}>选择 cookies.txt 文件</button>
      <button type="button" disabled={Boolean(busy) || !pasted.trim()} aria-busy={busy === 'paste'}
        onClick={() => { void run('paste', '/youtube-auth/cookies', { text: pasted }); }}>
        {busy === 'paste' ? '正在导入…' : '导入粘贴内容'}</button>
      <button type="button" onClick={onClose}>关闭</button>
    </div>
  </dialog>;
}
