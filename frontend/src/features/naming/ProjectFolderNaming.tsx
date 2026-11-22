import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import { cancelTask, isActive, pollJob } from '../../lib/jobs';
import type { AgentModelsState } from '../learning/useAgentModels';
import type { NamingJob } from './types';

type Plan = { id: string; project_id: string; scope: string; state: string; suggestion?: string; last_error?: string;
  recovery_status?: 'not_moved' | 'moved' | 'conflict'; recovery_outcome?: string; preview_token?: string; folder_preview?: { old: string; new: string; projects: number; files: number }; job_id?: string; active_job_id?: string };
type Inventory = { location: string; title: string; projects: number; latest: Plan | null };
type Props = { projectId: string; models: AgentModelsState; onBusyChange?: (busy: boolean) => void; initialTaskId: string | null; visible: boolean; onChanged: () => Promise<void>; onBeforeApply: () => void };
export default function ProjectFolderNaming({ projectId, models, onBusyChange, initialTaskId, visible, onChanged, onBeforeApply }: Props) {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [title, setTitle] = useState('');
  const [job, setJob] = useState<NamingJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [verified, setVerified] = useState('');
  const [restore, setRestore] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { setExpanded(false); }, [projectId, initialTaskId]);
  const active = useRef<AbortController | null>(null);
  const working = busy || isActive(job?.state);
  const recovery = !!plan && ['applying', 'recovery_required'].includes(plan.state);
  // The model picker now lives once at the top of the view, so the parent needs to know
  // when this panel is mid-operation to lock it.
  useEffect(() => { onBusyChange?.(working || recovery); }, [working, recovery, onBusyChange]);
  async function load(signal: AbortSignal) {
    const next = await request<Inventory>(`/projects/${projectId}/folder-naming`, signal);
    if (!signal.aborted) setInventory(next);
    return next;
  }
  async function readPlan(id: string, signal: AbortSignal) {
    const next = await request<Plan>(`/naming/plans/${id}`, signal);
    if (next.scope !== 'project_folder') throw new Error('这不是项目文件夹命名任务。');
    if (!signal.aborted) { setPlan(next); if (next.suggestion) setTitle(next.suggestion); setVerified(''); }
    return next;
  }
  async function follow(initial: NamingJob, signal: AbortSignal) {
    if (!initial.operation.startsWith('folder_')) throw new Error('这不是项目文件夹命名任务。');
    setJob(initial);
    const final = isActive(initial.state) ? await pollJob<NamingJob>(`/naming/tasks/${initial.id}`, signal, setJob) : initial;
    if (signal.aborted) return;
    setJob(final);
    const latest = await readPlan(final.result.plan_id, signal);
    await load(signal);
    if (final.state !== 'completed' && !['rolled_back', 'applied'].includes(latest.state) && !latest.last_error) setError(final.message);
    if (final.operation !== 'folder_plan') await onChanged();
  }
  async function perform(action: (signal: AbortSignal) => Promise<unknown>) {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller; setBusy(true); setError('');
    try { await action(controller.signal); }
    catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '操作未完成，没有自动重试。'); }
    finally { if (active.current === controller) { active.current = null; setBusy(false); } }
  }
  useEffect(() => {
    void perform(async signal => {
      const next = await load(signal);
      if (signal.aborted) return;
      if (initialTaskId) await follow(await request<NamingJob>(`/naming/tasks/${initialTaskId}`, signal), signal);
      else if (next.latest) {
        const latest = next.latest; await readPlan(latest.id, signal);
        if (latest.active_job_id && latest.state === 'analyzing') await follow(await request<NamingJob>(`/naming/tasks/${latest.active_job_id}`, signal), signal);
      }
    });
    return () => { active.current?.abort(); active.current = null; };
  }, [projectId, initialTaskId]);
  useEffect(() => { if (!visible) { setVerified(''); setRestore(false); } }, [visible]);
  // Only an already-applied plan may be compacted. Pending confirmation, recovery and errors stay visible.
  const canCollapse = plan?.state === 'applied' && !working && !error && !plan.last_error && !initialTaskId;
  const folderOpen = !canCollapse || expanded;
  return <section className="project-folder-naming" aria-labelledby="project-folder-title" aria-busy={working}>
    <div className="folder-naming-heading"><h2 id="project-folder-title">项目文件夹重命名</h2>
      {canCollapse && <button type="button" aria-expanded={folderOpen} aria-controls="folder-naming-body" onClick={() => setExpanded(!folderOpen)}>{folderOpen ? '收起文件夹编辑' : '编辑文件夹名称'}</button>}
    </div>
    {inventory && <p className="folder-current">当前文件夹 <code translate="no">{inventory.location}</code></p>}
    {!folderOpen && <p className="naming-hint" role="status">项目文件夹已重命名。</p>}
    <div id="folder-naming-body" className="folder-naming-body" hidden={!folderOpen}>
    <p className="sr-only">为整个项目生成简短、直观的名称。修改主文件夹并同步界面项目名称；原始来源标题、分集名称、视频与字幕文件名保持不变。</p>
    <p className="sr-only">点击生成即同意发送项目标题和已存分集名称，可能联网并产生费用；不发送视频、字幕正文或学习记录。</p>
    <div className="naming-actions"><button type="button" disabled={working || recovery || !inventory || !models.configured} onClick={() => {
      setVerified(''); void perform(async signal => follow(await request<NamingJob>(`/projects/${projectId}/folder-naming/plan`, signal, { consent: true, agent: models.selected }), signal));
    }}>AI 生成文件夹名</button></div>
    <label className="folder-title-label">新文件夹名称<input aria-label="新文件夹名称" value={title} maxLength={100} disabled={working || recovery}
      onChange={e => { setTitle(e.target.value); setVerified(''); }} /></label>
    <p className="sr-only">可直接手动填写。保留视频 ID 后缀以区分同名项目；确认前不会修改目录。</p>
    <button type="button" disabled={working || recovery || !inventory || !title.trim()} onClick={() => {
      const draft = title; setVerified(''); void perform(async signal => {
        const result = await request<Plan>(`/projects/${projectId}/folder-naming/preview`, signal, { title: draft });
        if (!signal.aborted) { setPlan(result); setVerified(draft); }
      });
    }}>预览文件夹改名</button>
    {plan?.folder_preview && <div className="folder-preview" aria-label="项目文件夹改名预览">
      <code translate="no">{plan.folder_preview.old}</code><span>→</span><code translate="no">{plan.folder_preview.new}</code>
      <p>包含 {plan.folder_preview.projects} 个归档项目、{plan.folder_preview.files} 个文件，内容保持不变。</p>
    </div>}
    {plan?.state === 'validated' && <button type="button" className="primary" disabled={!visible || working || verified !== title || !verified || !plan.preview_token}
      onClick={() => { onBeforeApply(); setVerified(''); void perform(async signal => follow(await request<NamingJob>(`/naming/plans/${plan.id}/apply`, signal, { confirm: true, preview_token: plan.preview_token }), signal)); }}>确认修改项目文件夹</button>}
    {plan?.state === 'applied' && <p role="status">项目文件夹已重命名。</p>}
    {plan?.state === 'rolled_back' && <p role="status">{plan.recovery_outcome === 'not_moved' ? '改名未执行，原文件完整保留；已解除失败状态，可重新预览。' : '已恢复原文件夹，可重新预览。'}</p>}
    {recovery && <div className="naming-recovery">{plan?.recovery_status === 'not_moved' && <p>原文件夹仍在，目标文件夹未生成。确认后将核对文件完整性并解除失败状态，不会重新改名或调用模型。</p>}<label><input type="checkbox" checked={restore} onChange={e => setRestore(e.target.checked)} />{plan?.recovery_status === 'not_moved' ? '确认核对文件并解除失败状态' : '确认恢复未完成的文件夹操作'}</label>
      <button type="button" disabled={working || !restore} onClick={() => { onBeforeApply(); void perform(async signal => follow(await request<NamingJob>(`/naming/plans/${plan!.id}/restore`, signal, { confirm: true }), signal)); }}>{plan?.recovery_status === 'not_moved' ? '核对并解除失败状态' : '恢复项目文件夹'}</button></div>}
    {job && isActive(job.state) && <div><p role="status">{job.message}</p><button type="button" disabled={job.phase === 'archiving'} onClick={() => { void cancelTask('naming', job.id).catch(e => setError(String(e))); }}>取消任务</button></div>}
    {plan?.last_error && <p role="alert">{plan.last_error}</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    </div>
  </section>;
}
