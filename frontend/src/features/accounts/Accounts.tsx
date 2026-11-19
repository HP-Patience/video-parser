import { useCallback, useEffect, useRef, useState } from 'react';
import Panel from '../../components/Panel';
import BiliLogin from '../subtitles/BiliLogin';
import QrLogin from './QrLogin';
import YoutubeLogin from './YoutubeLogin';
import { request } from '../../lib/api';
import './accounts.css';

// Cookie values never reach the browser: the API only reports presence, expiry and login progress.
type LoginSession = { id: string; mode: 'window' | 'qr'; state: 'starting' | 'waiting' | 'scanned' | 'saving' | 'saved' | 'failed' | 'cancelled' | 'expired' | 'verify'; message: string; started_at: number; qr?: string | null };
export type Account = {
  id: string; name: string; benefit: string; qr_login: boolean; qr_mode: 'bili' | 'browser' | null; manual_finish: boolean;
  import_cookies?: boolean;
  state: 'saved' | 'expired' | 'missing'; saved_at: number | null; expires_at: number | null;
  login: LoginSession | null; verified?: boolean | null; message?: string;
};
type Overview = { platforms: Account[]; storage: string };
const ACTIVE = ['starting', 'waiting', 'scanned', 'saving'];
const stateLabel: Record<Account['state'], string> = { saved: '已登录', expired: '已过期', missing: '未登录' };
const date = (seconds: number) => new Date(seconds * 1000).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });

export default function Accounts({ visible, focusPlatform, onChange }: { visible: boolean; focusPlatform?: string | null; onChange?: () => void }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [storage, setStorage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [qrOpen, setQrOpen] = useState<string | null>(null);
  const [youtubeOpen, setYoutubeOpen] = useState(false);
  const [confirmLogout, setConfirmLogout] = useState<string | null>(null);
  // Logged-in rows show one 重新登录; it expands into the two ways to log in only when asked.
  const [relogin, setRelogin] = useState<string | null>(null);
  const changed = useRef(onChange); changed.current = onChange;
  const previous = useRef<Record<string, string>>({});

  const load = useCallback(async (signal: AbortSignal) => {
    try {
      const data = await request<Overview>('/accounts', signal);
      if (signal.aborted) return;
      setAccounts(data.platforms); setStorage(data.storage); setError('');
      // A window that just finished saving changes what the workbench can parse.
      for (const item of data.platforms) {
        const key = `${item.state}:${item.login?.state || ''}`;
        if (previous.current[item.id] && previous.current[item.id] !== key && item.login?.state === 'saved') changed.current?.();
        previous.current[item.id] = key;
      }
    } catch (reason) {
      if (!signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取登录状态。');
    }
  }, []);
  const loginActive = accounts.some(item => item.login && ACTIVE.includes(item.login.state));
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    void load(controller.signal);
    const timer = loginActive ? setInterval(() => { void load(controller.signal); }, 1500) : undefined;
    return () => { controller.abort(); clearInterval(timer); };
  }, [visible, loginActive, load]);
  useEffect(() => {
    if (visible && focusPlatform) document.getElementById('account-' + focusPlatform)?.scrollIntoView({ block: 'center' });
  }, [visible, focusPlatform, accounts.length]);

  async function act(platform: string, action: 'login' | 'login/finish' | 'login/cancel' | 'check' | 'logout') {
    setBusy(platform + action); setNotes(value => ({ ...value, [platform]: '' }));
    try {
      const result = await request<Account>(`/accounts/${platform}/${action}`, new AbortController().signal, {});
      setAccounts(list => list.map(item => item.id === platform ? result : item));
      if (action === 'check' && result.message) setNotes(value => ({ ...value, [platform]: result.message || '' }));
      if (action === 'logout') { setNotes(value => ({ ...value, [platform]: '已删除本机登录信息。' })); changed.current?.(); }
    } catch (reason) {
      setNotes(value => ({ ...value, [platform]: reason instanceof Error ? reason.message : '操作失败，请重试。' }));
    } finally { setBusy(null); setConfirmLogout(null); }
  }

  return <Panel className="accounts-panel" aria-labelledby="page-title-accounts">
    {error && <p className="error-text" role="alert">{error}</p>}
    <ul className="account-list">{accounts.map(item => {
      // QR sessions run inside their own dialog; only window logins show progress in the row.
      const windowLogin = item.login?.mode === 'qr' ? null : item.login;
      const session = windowLogin && ACTIVE.includes(windowLogin.state) ? windowLogin : null;
      const ended = windowLogin && !session && windowLogin.state !== 'saved' ? windowLogin : null;
      const note = notes[item.id] || (ended ? ended.message : '') || (windowLogin?.state === 'saved' && item.state === 'saved' ? windowLogin.message : '');
      const other = loginActive && !session;
      return <li key={item.id} id={'account-' + item.id} className={focusPlatform === item.id ? 'account-focus' : undefined}>
        <div className="account-main">
          <div className="account-heading"><strong>{item.name}</strong>
            <span className={'account-state state-' + item.state}>{stateLabel[item.state]}</span>
</div>
          <p className="account-benefit">{item.benefit}</p>
          {item.state === 'saved' && <p className="account-meta">{item.saved_at ? `保存于 ${date(item.saved_at)}` : ''}{item.expires_at ? ` · 有效期至 ${date(item.expires_at)}` : ''}</p>}
          {session && <p className="account-progress" role="status" aria-live="polite">{session.message}</p>}
          {note && <p className="account-note" role="status">{note}</p>}
          {confirmLogout === item.id && <div className="account-confirm" role="alert">
            <span>删除本机保存的{item.name}登录信息？不影响已归档的文件。</span>
            <button type="button" className="danger-button" disabled={busy !== null} onClick={() => { void act(item.id, 'logout'); }}>确认退出</button>
            <button type="button" onClick={() => setConfirmLogout(null)}>取消</button>
          </div>}
        </div>
        <div className="account-actions">
          {item.import_cookies ? (session ? <>
            <button type="button" className="primary" disabled={busy !== null || session.state !== 'waiting'} onClick={() => { void act(item.id, 'login/finish'); }}>完成登录</button>
            <button type="button" disabled={busy !== null} onClick={() => { void act(item.id, 'login/cancel'); }}>取消</button>
          </> : <>
            {item.state === 'saved' ? <>
              <button type="button" disabled={busy !== null} aria-busy={busy === item.id + 'check'} onClick={() => { void act(item.id, 'check'); }}>检查</button>
              <button type="button" disabled={busy !== null || other} aria-busy={busy === item.id + 'login'}
                onClick={() => { void act(item.id, 'login'); }}>重新登录</button>
            </> : <button type="button" className="primary" disabled={busy !== null || other} aria-busy={busy === item.id + 'login'}
              onClick={() => { void act(item.id, 'login'); }}>登录窗口</button>}
            <button type="button" disabled={busy !== null} onClick={() => setYoutubeOpen(true)}>导入 cookies</button>
            {item.state !== 'missing' && <button type="button" disabled={busy !== null} onClick={() => setConfirmLogout(item.id)}>退出登录</button>}
          </>) : session ? <>
            <button type="button" className="primary" disabled={busy !== null || session.state !== 'waiting'} onClick={() => { void act(item.id, 'login/finish'); }}>完成登录</button>
            <button type="button" disabled={busy !== null} onClick={() => { void act(item.id, 'login/cancel'); }}>取消</button>
          </> : <>
            {item.state === 'saved' && relogin !== item.id ? <>
              <button type="button" disabled={busy !== null} aria-busy={busy === item.id + 'check'} onClick={() => { void act(item.id, 'check'); }}>检查</button>
              <button type="button" disabled={busy !== null || other} aria-busy={busy === item.id + 'login'}
                onClick={() => { if (item.qr_login) setRelogin(item.id); else void act(item.id, 'login'); }}>重新登录</button>
            </> : <>
              {item.qr_login && <button type="button" className="primary" disabled={busy !== null || other} onClick={() => { setRelogin(null); setQrOpen(item.id); }}>扫码登录</button>}
              <button type="button" className={item.qr_login ? undefined : 'primary'} disabled={busy !== null || other} aria-busy={busy === item.id + 'login'}
                title={item.qr_login ? '扫码后平台要求短信验证时使用：在本机弹出浏览器窗口登录，完成后自动保存' : undefined}
                onClick={() => { setRelogin(null); void act(item.id, 'login'); }}>{item.qr_login ? '浏览器登录' : '打开登录窗口'}</button>
              {relogin === item.id && <button type="button" onClick={() => setRelogin(null)}>取消</button>}
            </>}
            {item.state !== 'missing' && relogin !== item.id && <button type="button" disabled={busy !== null} onClick={() => setConfirmLogout(item.id)}>退出登录</button>}
          </>}
        </div>
      </li>;
    })}</ul>
    {!accounts.length && !error && <p className="notice">正在读取登录状态…</p>}
    <p className="accounts-footnote">登录信息只保存在本机{storage ? `（${storage.split('（')[0]}）` : ''}。YouTube 需要导入浏览器登录态。</p>
    {youtubeOpen && <YoutubeLogin onClose={() => setYoutubeOpen(false)}
      onSuccess={() => { setNotes(value => ({ ...value, youtube: 'YouTube 登录态已保存到本机。' })); void load(new AbortController().signal); changed.current?.(); }} />}
    {qrOpen === 'bilibili' && <BiliLogin onClose={() => setQrOpen(null)} onSuccess={() => { setQrOpen(null); setNotes(value => ({ ...value, bilibili: 'B站扫码登录成功，已保存到本机。' })); void load(new AbortController().signal); changed.current?.(); }} />}
    {qrOpen && qrOpen !== 'bilibili' && (() => {
      const account = accounts.find(item => item.id === qrOpen);
      if (!account) return null;
      const close = () => { setQrOpen(null); void load(new AbortController().signal); };
      return <QrLogin account={account} onClose={close}
        onSuccess={() => { setQrOpen(null); setNotes(value => ({ ...value, [account.id]: `${account.name}扫码登录成功，已保存到本机。` })); void load(new AbortController().signal); changed.current?.(); }}
        onFallback={() => { setQrOpen(null); void act(account.id, 'login'); }} />;
    })()}
  </Panel>;
}
