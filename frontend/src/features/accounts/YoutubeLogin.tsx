import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';

type Status = { configured: boolean; cookies: number; complete: boolean; browser: string | null; updated_at: number | null; source: 'file' | 'browser' | null };

const BROWSERS: [string, string][] = [['chrome', 'Chrome'], ['edge', 'Edge'], ['firefox', 'Firefox'], ['brave', 'Brave']];
const STORE = 'https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc';

// YouTube sign-in is imported from the reader's own browser session; nothing is typed into this app.
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

  async function pick(file: File | undefined) {
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) { setError('文件过大，请只导出 Google 与 YouTube 两个站点。'); return; }
    setError('');
    setPasted(await file.text());
  }

  return <dialog ref={dialog} className="settings-dialog login-dialog youtube-import" aria-labelledby="youtube-login-title" onCancel={event => { event.preventDefault(); onClose(); }}>
    <h2 id="youtube-login-title">导入 YouTube 登录态</h2>
    <p className="login-status" role="status" aria-live="polite">
      {status === null ? '正在读取…'
        : status.configured
          ? `已配置：${status.source === 'browser' ? '跟随浏览器登录态' : '已导入 cookies 文件'}（${status.cookies} 条 cookie）`
          : '未配置：YouTube 视频会被要求登录校验。'}
    </p>
    {status?.configured && !status.complete && <p className="error-text" role="alert">
      缺少 SID / __Secure-1PSID 等 Google 身份 cookie：解析能用，但浏览器里仍是未登录。导出通常读不到这几项，浏览器内播放请改用「登录窗口」。
    </p>}

    <section className="youtube-guide" aria-labelledby="youtube-guide-title">
      <h3 id="youtube-guide-title">按这三步导入</h3>
      <ol>
        <li>安装浏览器扩展 <a href={STORE} target="_blank" rel="noreferrer">Get cookies.txt LOCALLY</a>（免费、开源，只在本机读写）</li>
        <li>在已登录 YouTube 的浏览器里点扩展图标，选 <strong>Export All Cookies</strong>：只导出 youtube.com 会缺少 Google 身份 cookie</li>
        <li>回到这里导入，其他站点的 cookie 会被自动丢弃，只保留 Google 与 YouTube 的条目</li>
      </ol>
      <p className="notice">不想装扩展的话，关掉这个窗口，直接点 YouTube 那行的「登录窗口」在应用内登录一次，效果一样。</p>
    </section>

    <label htmlFor="youtube-cookies">粘贴 cookies.txt 内容（Netscape 格式，也支持扩展导出的 JSON）</label>
    <textarea id="youtube-cookies" rows={5} spellCheck={false} value={pasted} disabled={Boolean(busy)}
      onChange={event => setPasted(event.target.value)} placeholder="# Netscape HTTP Cookie File…" />
    {error && <p className="error-text" role="alert">{error}</p>}
    <input ref={picker} type="file" accept=".txt,.json,text/plain,application/json" hidden
      onChange={event => { void pick(event.target.files?.[0]); event.target.value = ''; }} />
    <div className="login-actions">
      <button type="button" disabled={Boolean(busy)} onClick={() => picker.current?.click()}>选择 cookies.txt 文件</button>
      <button type="button" disabled={Boolean(busy) || !pasted.trim()} aria-busy={busy === 'paste'}
        onClick={() => { void run('paste', '/youtube-auth/cookies', { text: pasted }); }}>
        {busy === 'paste' ? '正在导入…' : '导入粘贴内容'}</button>
      <button type="button" onClick={onClose}>关闭</button>
    </div>

    <details className="youtube-advanced">
      <summary>进阶：直接读取本机浏览器</summary>
      <p className="notice">Chrome / Edge 运行时会锁定数据库，而且 127 版以后 cookie 改为系统级加密，这条路多半失败——失败属于预期，请用上面的方式。</p>
      <div className="youtube-sources">
        {BROWSERS.map(([value, label]) => <button type="button" key={value} disabled={Boolean(busy)}
          aria-busy={busy === value} onClick={() => { void run(value, '/youtube-auth/import', { browser: value }); }}>
          {busy === value ? `正在读取 ${label}…` : `从 ${label} 导入`}</button>)}
      </div>
    </details>
  </dialog>;
}
