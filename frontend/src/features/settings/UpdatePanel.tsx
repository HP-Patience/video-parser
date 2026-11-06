import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { UpdateInstallResult, UpdateStatus } from '../../lib/api';

type Busy = 'check' | 'install' | null;
type Notice = { text: string; error?: boolean } | null;

export default function UpdatePanel({ currentVersion }: { currentVersion: string }) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function check() {
    if (busy) return;
    const controller = new AbortController(); active.current = controller;
    setBusy('check'); setNotice(null);
    try {
      const result = await request<UpdateStatus>('/updates/check', controller.signal);
      if (controller.signal.aborted) return;
      setStatus(result);
      if (!result.release_found) setNotice({ text: '尚未找到已发布的更新版本。' });
      else if (result.available) setNotice({ text: result.can_install
        ? `发现新版本 ${result.latest_version}。确认后会关闭应用、自动安装并重新打开。`
        : '发现新版本，但当前运行方式不能直接覆盖；请前往发布页下载安装版。' });
      else setNotice({ text: '已是最新版本。' });
    } catch (reason) {
      if (!controller.signal.aborted) setNotice({ text: reason instanceof Error ? reason.message : '检查更新失败，请重试。', error: true });
    } finally {
      if (!controller.signal.aborted) { active.current = null; setBusy(null); }
    }
  }

  async function install() {
    if (busy || !status?.can_install) return;
    const controller = new AbortController(); active.current = controller;
    setBusy('install'); setNotice(null);
    try {
      const result = await request<UpdateInstallResult>('/updates/install', controller.signal, { confirm: true });
      if (!controller.signal.aborted) setNotice({ text: `更新程序已启动（v${result.version}），应用即将关闭并重新打开。` });
    } catch (reason) {
      if (controller.signal.aborted) return;
      setNotice({ text: reason instanceof Error ? reason.message : '启动更新失败，请重试。', error: true });
      setBusy(null); active.current = null;
    }
  }

  const current = status?.current_version || currentVersion || '未知';
  return <section className="settings-card settings-card-wide update-panel" aria-labelledby="settings-update">
    <div className="update-summary">
      <div>
        <h3 id="settings-update">版本更新</h3>
        <p className="settings-hint">当前版本 <code>{current}</code>。检查会读取 GitHub 最新正式版；不会自动下载或安装。</p>
      </div>
      <div className="settings-actions">
        <button type="button" disabled={busy !== null} aria-busy={busy === 'check'} onClick={() => { void check(); }}>
          {busy === 'check' ? '正在检查…' : '检查更新'}
        </button>
        {status?.available && status.can_install && <button type="button" className="primary" disabled={busy !== null}
          aria-busy={busy === 'install'} onClick={() => { void install(); }}>
          {busy === 'install' ? '正在启动更新…' : `更新到 ${status.latest_version}`}
        </button>}
        {status?.available && !status.can_install && <a className="button" href={status.release_url} target="_blank" rel="noreferrer">前往发布页</a>}
      </div>
    </div>
    {notice && <p role="status" aria-live="polite" className={`update-notice${notice.error ? ' is-error' : ''}`}>{notice.text}</p>}
  </section>;
}
