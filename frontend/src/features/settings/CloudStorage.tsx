import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { request } from '../../lib/api';
import { formatBytes, formatEta } from '../../lib/format';
import './cloud-storage.css';

type Session = { logged_in: boolean; state: string; server: string; login: string };
type Version = { version_id: string; sha256: string; size: number; created_at: number; details?: { filename?: string } };
type Material = { material_id: string; kind: string; current_version_id: string; versions: Version[] };
type LocalProject = { id: string; title: string; sync_enabled: boolean; metadata_conflicts?: Record<string, { local: unknown; cloud: unknown }> };
type CloudProject = { id: string; title: string; revision: number; deleted_at: number | null; materials: Material[] };
type TaskSummary = { version_count?: number; byte_count?: number; revision_base?: number; full?: boolean; material_id?: string; version_id?: string; field?: string; choice?: string; local_project_id?: string; project_count?: number };
type Transfer = { id: string; project_id: string; action: string; status: string; attempts: number; error: string;
  project_title?: string; summary?: TaskSummary; next_at?: number; created_at?: number; started_at?: number; finished_at?: number };
const STATES: Record<string, string> = { queued: '等待处理', running: '传输中', retry: '等待重试', retrying: '等待重试',
  completed: '已完成', failed: '失败', cancelled: '已取消', conflict: '需要处理冲突', blocked: '等待处理', waiting_login: '等待登录' };
const ACTIONS: Record<string, string> = { upload: '上传', pull: '下载', refresh: '同步列表', associate: '关联项目', 'resolve-metadata': '处理状态冲突', delete: '移入回收站', restore: '恢复', 'select-version': '切换版本', image: '下载材料' };

const KINDS: Record<string, string> = { subtitle: '字幕', transcription: '转录', correction: '校对', translation: '翻译', cover: '封面', image: '图片', frame: '画面帧', analysis: 'AI 分析', 'analysis-followup': '分析追问', conversation: '对话', 'conversation-message': '对话消息', ai_text: 'AI 文本', ai_result: 'AI 结果' };

const pad = (value: number) => String(value).padStart(2, '0');
// Server timestamps are epoch seconds; show them in the browser's local time.
function formatMoment(seconds?: number) {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Facts worth showing for one queued task; labels are translated by the DOM pass.
function transferFacts(job: Transfer) {
  const facts: { label: string; value: string }[] = [];
  const created = formatMoment(job.created_at);
  if (created) facts.push({ label: '创建时间', value: created });
  const started = formatMoment(job.started_at);
  if (started) facts.push({ label: '开始时间', value: started });
  const finished = formatMoment(job.finished_at);
  if (finished) facts.push({ label: '结束时间', value: finished });
  if (job.started_at && job.finished_at && job.finished_at >= job.started_at) facts.push({ label: '耗时', value: formatEta(job.finished_at - job.started_at) });
  if (job.attempts > 0) facts.push({ label: '尝试次数', value: String(job.attempts) });
  const waiting = formatMoment(job.next_at);
  if (waiting && ['retry', 'retrying', 'queued'].includes(job.status)) facts.push({ label: '下次重试', value: waiting });
  const summary = job.summary || {};
  if (summary.version_count !== undefined) facts.push({ label: '版本数量', value: String(summary.version_count) });
  if (summary.byte_count !== undefined) facts.push({ label: '数据量', value: formatBytes(summary.byte_count) });
  if (summary.revision_base !== undefined) facts.push({ label: '基于修订', value: String(summary.revision_base) });
  if (job.action === 'pull' || job.action === 'image') facts.push({ label: '下载范围', value: summary.full ? '全量' : '按需' });
  if (summary.material_id) facts.push({ label: '材料 ID', value: summary.material_id.slice(0, 12) });
  if (job.action === 'select-version' && summary.version_id) facts.push({ label: '版本 ID', value: summary.version_id.slice(0, 12) });
  if (summary.field) facts.push({ label: '冲突字段', value: summary.field === 'learned' ? '学习状态' : '标题' });
  if (summary.choice) facts.push({ label: '处理方式', value: summary.choice === 'local' ? '保留本机值' : '采用云端值' });
  if (summary.local_project_id) facts.push({ label: '本机项目 ID', value: summary.local_project_id.slice(0, 12) });
  if (summary.project_count !== undefined) facts.push({ label: '同步项目数', value: String(summary.project_count) });
  return facts;
}

function TransferFacts({ job }: { job: Transfer }) {
  const facts = transferFacts(job);
  if (!facts.length) return null;
  return <dl className="cloud-facts">{facts.map(fact => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>;
}

export default function CloudStorage({ visible }: { visible: boolean }) {
  const [session, setSession] = useState<Session | null>(null);
  const [server, setServer] = useState('');
  const [loginName, setLoginName] = useState('');
  const [password, setPassword] = useState('');
  const [local, setLocal] = useState<LocalProject[]>([]);
  const [cloud, setCloud] = useState<CloudProject[]>([]);
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [associateCloud, setAssociateCloud] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [recycle, setRecycle] = useState(false);
  const [versions, setVersions] = useState<{ project: CloudProject; materials: Material[] } | null>(null);
  const generation = useRef(0);
  const owner = useRef('');
  const mounted = useRef(true);
  const allSelected = local.length > 0 && local.every(project => selected.includes(project.id));

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++; }; }, []);

  async function refresh(signal: AbortSignal) {
    const current = ++generation.current;
    const account = await request<Session>('/cloud/session', signal);
    if (signal.aborted || current !== generation.current) return;
    const identity = account.logged_in ? account.server + '\n' + account.login : '';
    if (identity !== owner.current) {
      owner.current = identity; setSelected([]); setAssociateCloud(''); setVersions(null); setLocal([]); setCloud([]); setTransfers([]);
    }
    setSession(account);
    if (!account.logged_in) return;
    const [projects, jobs] = await Promise.all([
      request<{ local: LocalProject[]; cloud: CloudProject[] }>('/cloud/projects', signal),
      request<{ transfers: Transfer[] }>('/cloud/transfers', signal),
    ]);
    if (signal.aborted || current !== generation.current) return;
    setLocal(projects.local); setCloud(projects.cloud); setTransfers(jobs.transfers);
  }
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(controller.signal); }
      catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取云端状态。'); }
      if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); generation.current++; };
  }, [visible]);

  async function run(operation: () => Promise<void>, message: string) {
    if (busy) return;
    setBusy(true); setError(''); setNotice(''); generation.current++;
    try {
      await operation();
      if (!mounted.current) return;
      setNotice(message);
      await refresh(new AbortController().signal);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : '操作未完成，请稍后重试。');
    } finally { if (mounted.current) setBusy(false); }
  }
  const post = (path: string, body: unknown) => request<unknown>('/cloud' + path, new AbortController().signal, body);
  function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(async () => {
      try { await post('/login', { server, login: loginName, password }); } finally { setPassword(''); }
    }, '已登录。请选择要上传或自动同步的本地项目。');
  }
  function upload(sync: boolean, independent = false) {
    if (independent && !window.confirm('将所选材料上传为独立的云端项目？之后与原项目分别管理。')) return;
    void run(async () => {
      for (const project_id of selected) {
        if (sync) await post('/sync-mode', { project_id, enabled: true });
        else await post('/upload', { project_id, independent });
      }
      setSelected([]);
    }, sync ? '所选项目已开启自动同步。' : '所选项目已加入上传队列。');
  }
  function viewVersions(project: CloudProject) {
    void run(async () => {
      const result = await request<{ materials: Material[] }>('/cloud/versions?project_id=' + encodeURIComponent(project.id), new AbortController().signal);
      setVersions({ project, materials: result.materials });
    }, '已读取材料版本。');
  }
  return <div className="settings-section-stack cloud-storage">
    <section className="settings-card" aria-labelledby="cloud-account-title">
      <h3 id="cloud-account-title">登录与云端存储</h3>
      <p className="settings-hint">保存视频链接、字幕、AI 材料和图片。视频与音频留在本机；每台设备独立生成材料。</p>
      {session === null ? <p role="status">正在读取登录状态…</p> : session.logged_in ? <>
        <p className="cloud-account">{session.login}<span>{session.server}</span></p>
        <div className="settings-actions"><button type="button" disabled={busy} onClick={() => { void run(async () => { await post('/logout', {}); setVersions(null); }, '已退出登录，已下载的本地材料仍然保留。'); }}>退出登录</button></div>
      </> : <form onSubmit={login}>
        <label htmlFor="cloud-server">云服务地址</label>
        <input id="cloud-server" type="url" required placeholder="https://cloud.example.com 或 http://203.0.113.10:8090" autoComplete="url" value={server} onChange={event => setServer(event.target.value)} disabled={busy} />
        <label htmlFor="cloud-login">用户名</label>
        <input id="cloud-login" type="text" required placeholder="Celyn" autoComplete="username" value={loginName} onChange={event => setLoginName(event.target.value)} disabled={busy} />
        <label htmlFor="cloud-password">密码</label>
        <input id="cloud-password" type="password" required autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={busy} />
        <div className="settings-actions"><button type="submit" disabled={busy}>{busy ? '正在登录…' : '登录'}</button></div>
        <p className="settings-hint">账号由自有服务器管理员创建。本机服务重启后需要重新登录。</p>
      </form>}
      {error && <p className="cloud-notice" role="alert">{error}</p>}
      {notice && <p className="cloud-notice" role="status">{notice}</p>}
    </section>
    {session?.logged_in && <>
      <section className="settings-card" aria-labelledby="cloud-local-title">
        <h3 id="cloud-local-title">本地项目</h3>
        <p className="settings-hint">单次上传只保存这次材料；自动同步会持续上传后续修改。未选择的项目保留在本机。</p>
        {local.length > 0 && <label className="settings-check cloud-select-all"><input type="checkbox" checked={allSelected} disabled={busy}
          onChange={event => setSelected(event.target.checked ? local.map(project => project.id) : [])} /><span>全选（{selected.length} / {local.length}）</span></label>}
        <div className="settings-actions"><button type="button" disabled={busy || !selected.length} onClick={() => upload(false)}>上传所选（{selected.length}）</button>
          <button type="button" disabled={busy || !selected.length} onClick={() => upload(true)}>开启所选自动同步</button>
          <button type="button" disabled={busy || !selected.length} onClick={() => upload(false, true)}>上传为独立项目</button></div>
        <ul className="cloud-list">{local.map(project => <li key={project.id}>
          <label className="settings-check"><input type="checkbox" checked={selected.includes(project.id)} disabled={busy} onChange={event => setSelected(value => event.target.checked ? [...value, project.id] : value.filter(id => id !== project.id))} /><span>{project.title}</span></label>
          <span>{project.sync_enabled ? '自动同步中' : '仅本地 / 手动上传'}</span>
          {Object.entries(project.metadata_conflicts || {}).map(([field, values]) => <div className="cloud-conflict" key={field}>
            <p className="settings-hint">{field === 'learned' ? '学习状态' : '标题'}发生冲突，本机：{String(values.local)}；云端：{String(values.cloud)}。两边的值已保留，请选择要使用的值。</p>
            <div className="settings-actions">{(['local', 'cloud'] as const).map(choice => <button type="button" key={choice} disabled={busy} onClick={() => {
              void run(async () => { await post('/resolve-metadata', { project_id: project.id, field, choice }); }, '冲突处理已加入队列。');
            }}>{choice === 'local' ? '保留本机值并上传' : '采用云端值'}</button>)}</div>
          </div>)}
          {project.sync_enabled && <button type="button" disabled={busy} onClick={() => { void run(async () => { await post('/sync-mode', { project_id: project.id, enabled: false }); }, '已停止此项目自动同步。'); }}>停止自动同步</button>}
        </li>)}</ul>
        {!local.length && <p className="settings-hint">本机还没有归档项目。</p>}
        {local.length > 0 && cloud.some(project => !project.deleted_at) && <div className="cloud-associate">
          <label htmlFor="cloud-associate">关联已有云端项目</label>
          <p className="settings-hint">本地与云端出现重复项目时，勾选一个本地项目并选择同一视频的云端项目。关联会保留双方材料。</p>
          <select id="cloud-associate" value={associateCloud} onChange={event => setAssociateCloud(event.target.value)} disabled={busy}>
            <option value="">选择已有云端项目</option>
            {cloud.filter(project => !project.deleted_at).map(project => <option key={project.id} value={project.id}>{project.title}</option>)}
          </select>
          <div className="settings-actions"><button type="button" disabled={busy || selected.length !== 1 || !associateCloud} onClick={() => {
            if (window.confirm('将勾选的本地项目与此云端项目关联？仅允许关联同一视频，双方材料会保留。')) void run(async () => {
              await post('/associate', { local_project_id: selected[0], cloud_project_id: associateCloud }); setSelected([]); setAssociateCloud('');
            }, '关联请求已加入队列。');
          }}>关联并保留双方材料</button></div>
        </div>}
      </section>
      <section className="settings-card" aria-labelledby="cloud-project-title">
        <div className="cloud-heading"><h3 id="cloud-project-title">云端项目</h3><button type="button" aria-pressed={recycle} onClick={() => setRecycle(value => !value)}>{recycle ? '返回项目' : '回收站'}</button></div>
        <p className="settings-hint">登录后同步文字和状态；图片可按需下载，也可以下载全部材料以便离线使用。</p>
        <ul className="cloud-list">{cloud.filter(project => Boolean(project.deleted_at) === recycle).map(project => <li key={project.id}>
          <div className="cloud-project-name"><strong>{project.title}</strong><span>版本 {project.revision}</span></div>
          <div className="settings-actions">{project.deleted_at ? <button type="button" disabled={busy} onClick={() => { void run(async () => { await post('/restore', { project_id: project.id }); }, '恢复请求已加入队列。'); }}>恢复项目</button> : <>
            <button type="button" disabled={busy} onClick={() => { void run(async () => { await post('/pull', { project_id: project.id, full: false }); }, '文字与状态同步已加入队列。'); }}>同步文字与状态</button>
            <button type="button" disabled={busy} onClick={() => { void run(async () => { await post('/pull', { project_id: project.id, full: true }); }, '全部材料下载已加入队列。'); }}>下载全部材料</button>
            <button type="button" disabled={busy} onClick={() => viewVersions(project)}>材料版本</button>
            <button type="button" disabled={busy} onClick={() => {
              if (window.confirm(`将“${project.title}”移入云端回收站？30 天内可恢复，本地材料会保留。`)) void run(async () => { await post('/delete', { project_id: project.id }); }, '删除请求已加入队列。');
            }}>移入回收站</button>
          </>}</div>
        </li>)}</ul>
        {!cloud.some(project => Boolean(project.deleted_at) === recycle) && <p className="settings-hint">{recycle ? '回收站为空。' : '暂无云端项目。'}</p>}
      </section>
      {versions && <section className="settings-card" aria-labelledby="cloud-version-title">
        <div className="cloud-heading"><h3 id="cloud-version-title">{versions.project.title} · 材料版本</h3><button type="button" onClick={() => setVersions(null)}>收起</button></div>
        <p className="settings-hint">上传其他版本会保留当前材料。切换会修改云端当前版本；其他设备已有的本地选择会保留。</p>
        <ul className="cloud-list">{versions.materials.map(material => <li key={material.material_id}><div><strong>{KINDS[material.kind] || '材料'}</strong><small>{material.versions.find(version => version.version_id === material.current_version_id)?.details?.filename || material.material_id}</small></div>
          <ul className="cloud-versions">{material.versions.map(version => <li key={version.version_id}>
            <span>{version.sha256.slice(0, 12)} · {Math.ceil(version.size / 1024)} KB {version.version_id === material.current_version_id ? '· 当前' : ''}</span>
            <div className="settings-actions"><button type="button" disabled={busy || version.version_id === material.current_version_id} onClick={() => {
              if (window.confirm('将这个版本设为云端当前材料？')) void run(async () => { await post('/select-version', { project_id: versions.project.id, material_id: material.material_id, version_id: version.version_id }); setVersions(null); }, '切换版本已加入队列。');
            }}>设为云端当前</button>
              <button type="button" disabled={busy} onClick={() => { void run(async () => { await post('/image', { project_id: versions.project.id, material_id: material.material_id, version_id: version.version_id }); }, '此版本下载已加入队列。'); }}>下载此版本</button></div>
          </li>)}</ul>
        </li>)}</ul>
        {!versions.materials.length && <p className="settings-hint">尚未上传材料。</p>}
      </section>}
      <section className="settings-card" aria-labelledby="cloud-transfer-title"><h3 id="cloud-transfer-title">传输记录</h3>
        <ul className="cloud-list">{transfers.map(job => <li key={job.id}><div className="cloud-transfer">
          <strong>{ACTIONS[job.action] || job.action} · {STATES[job.status] || job.status}</strong>
          <small className="cloud-transfer-project">{job.project_title || (job.action === 'refresh' ? '全部云端项目' : job.project_id)}</small>
          <TransferFacts job={job} />
          {job.error && <p className="settings-hint">{job.error}</p>}
        </div>
          <div className="settings-actions">{['failed', 'conflict', 'retry', 'blocked'].includes(job.status) && <button type="button" disabled={busy} onClick={() => { void run(async () => { await post('/transfers/retry', { task_id: job.id }); }, '已重新加入队列。'); }}>重试</button>}
            {!['completed', 'cancelled'].includes(job.status) && <button type="button" disabled={busy} onClick={() => { void run(async () => { await post('/transfers/cancel', { task_id: job.id }); }, '已请求取消。'); }}>取消</button>}</div>
        </li>)}</ul>
        {!transfers.length && <p className="settings-hint">暂无传输任务。</p>}
      </section>
    </>}
  </div>;
}
