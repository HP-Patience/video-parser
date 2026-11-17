import { useEffect, useRef, useState } from 'react';
import ProgressBar from '../../components/ProgressBar';
import { request } from '../../lib/api';
import type { Configuration, UpdateInstallResult, UpdateProgress, UpdateStatus } from '../../lib/api';

type Busy = 'check' | 'install' | null;
type Notice = { text: string; error?: boolean } | null;
const updating = (progress: UpdateProgress | null) => !!progress && !['idle', 'failed'].includes(progress.state);

export default function UpdatePanel({ currentVersion }: { currentVersion: string }) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [pollGeneration, setPollGeneration] = useState(0);
  const [disconnected, setDisconnected] = useState(false);
  const active = useRef<AbortController | null>(null);
  const applying = useRef(false);
  const targetVersion = useRef<string | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    let resumed = false;
    setDisconnected(false);
    async function poll() {
      try {
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(6000)]);
        const result = await request<UpdateProgress>('/updates/status', signal);
        if (controller.signal.aborted) return;
        if (!result.state) return; // An older backend has no progress endpoint.
        if (result.version) targetVersion.current = result.version;
        if (result.state === 'applying') applying.current = true;
        if (applying.current && result.state === 'idle') {
          const config = await request<Configuration>('/config', signal);
          if (config.version === targetVersion.current) { window.location.reload(); return; }
        } else {
          setProgress(result);
          if (updating(result)) { resumed = true; setBusy('install'); }
          if (result.state === 'failed') {
            setBusy(null);
            setNotice({ text: result.error || '更新失败，原有数据已保留，请重试。', error: true });
            return;
          }
          if (result.state === 'idle') return;
        }
        failures = 0;
      } catch {
        if (controller.signal.aborted) return;
        if (applying.current) {
          try {
            const config = await request<Configuration>('/config', AbortSignal.any([controller.signal, AbortSignal.timeout(3000)]));
            if (config.version === targetVersion.current) { window.location.reload(); return; }
          } catch { /* The backend is unavailable while its files are replaced. */ }
        }
        if (++failures >= 15) { setDisconnected(true); return; }
        if (!targetVersion.current && !resumed) return;
      }
      if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 1000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [pollGeneration]);

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
        ? `发现新版本 ${result.latest_version}。点击更新后会下载、校验并自动重启，保留现有数据。`
        : result.install_reason === 'source'
          ? '当前是源码开发目录，不能覆盖源代码。请使用桌面版的应用内更新。'
          : '该版本暂不支持当前系统的应用内更新。' });
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
    setBusy('install'); setNotice(null); setDisconnected(false);
    applying.current = false;
    targetVersion.current = status.latest_version;
    setProgress({ state: 'checking', version: status.latest_version, downloaded_bytes: 0, total_bytes: null, error: null });
    try {
      const result = await request<UpdateInstallResult>('/updates/install', controller.signal, { confirm: true });
      if (!controller.signal.aborted) {
        if (result.version) targetVersion.current = result.version;
        setPollGeneration(value => value + 1);
      }
    } catch (reason) {
      if (controller.signal.aborted) return;
      setProgress(null);
      setNotice({ text: reason instanceof Error ? reason.message : '启动更新失败，请重试。', error: true });
      setBusy(null);
    } finally {
      if (!controller.signal.aborted) active.current = null;
    }
  }

  const current = status?.current_version || currentVersion || '未知';
  const stateText = progress?.state === 'checking' ? '正在准备更新…'
    : progress?.state === 'downloading' ? '正在下载更新…'
      : progress?.state === 'verifying' ? '正在校验更新包…'
        : progress?.state === 'applying' ? '正在安装并重启，请稍候…' : '';
  return <section className="settings-card settings-card-wide update-panel" aria-labelledby="settings-update">
    <div className="update-summary">
      <div>
        <h3 id="settings-update">版本更新</h3>
        <p className="settings-hint">当前版本 <code>{current}</code></p>
      </div>
      <div className="settings-actions">
        <button type="button" disabled={busy !== null} aria-busy={busy === 'check'} onClick={() => { void check(); }}>
          {busy === 'check' ? '正在检查…' : '检查更新'}
        </button>
        {status?.available && status.can_install && <button type="button" className="primary" disabled={busy !== null}
          aria-busy={busy === 'install'} onClick={() => { void install(); }}>
          {busy === 'install' ? '正在更新…' : `更新到 ${status.latest_version}`}
        </button>}
        {disconnected && <button type="button" onClick={() => { setPollGeneration(value => value + 1); }}>重新连接</button>}
      </div>
    </div>
    {updating(progress) && <div aria-live="polite">
      <p className="update-notice" role="status">{stateText}</p>
      {progress?.state === 'downloading' && <ProgressBar progress={{ bytes: progress.downloaded_bytes, total_bytes: progress.total_bytes || undefined }} />}
    </div>}
    {disconnected && <p className="update-notice" role="status">暂时无法连接应用，请重新连接以查看更新结果。</p>}
    {notice && <p role="status" aria-live="polite" className={`update-notice${notice.error ? ' is-error' : ''}`}>{notice.text}</p>}
  </section>;
}
