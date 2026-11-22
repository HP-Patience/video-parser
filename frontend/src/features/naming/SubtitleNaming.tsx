import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { Configuration } from '../../lib/api';
import { cancelTask, isActive, pollJob } from '../../lib/jobs';
import AgentModelPicker from '../learning/AgentModelPicker';
import useAgentModels from '../learning/useAgentModels';
import type { NamingEntry, NamingInventory, NamingJob, NamingPlan, NamingPreview, NamingRow } from './types';
import './subtitle-naming.css';
import ProjectFolderNaming from './ProjectFolderNaming';

type Props = { projectId: string; config: Configuration | null; initialTaskId?: string | null; visible: boolean;
  onChanged: () => Promise<void>; onBeforeApply: () => void; onOpenSettings: (button: HTMLButtonElement) => void };
const states: Record<string, string> = { analyzing: '生成新名字中', proposed: '新名字待确认', validated: '新名字待确认', applying: '文件改名未完成',
  applied: '文件重命名完成', rolled_back: '已恢复原命名', recovery_required: '文件改名未完成', failed: '未生成建议，没有改名', stale: '来源已变化，不可执行' };
const ready = (entry: NamingEntry) => entry.files.length > 0 && !!entry.transcript;

export default function SubtitleNaming({ projectId, config, initialTaskId, visible, onChanged, onBeforeApply, onOpenSettings }: Props) {
  const container = useRef<HTMLElement>(null);
  const active = useRef<AbortController | null>(null);
  const cancelRequest = useRef<AbortController | null>(null);
  const focusedDraft = useRef('');
  const [folderTask, setFolderTask] = useState<string | null>(null);
  const [folderBusy, setFolderBusy] = useState(false);
  const [inventory, setInventory] = useState<NamingInventory | null>(null);
  const inventoryRef = useRef<NamingInventory | null>(null);
  const [plan, setPlan] = useState<NamingPlan | null>(null);
  const [rows, setRows] = useState<NamingRow[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<NamingPreview | null>(null);
  const [previewFor, setPreviewFor] = useState('');
  const [validating, setValidating] = useState(false);
  const [validationError, setValidationError] = useState('');
  const [validationAttempt, setValidationAttempt] = useState(0);
  const [job, setJob] = useState<NamingJob | null>(null);
  const [pending, setPending] = useState(false);
  const [restoreConfirm, setRestoreConfirm] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState('');
  const models = useAgentModels(config?.ai || null, !!config?.ai_configured);
  const working = pending || isActive(job?.state);
  const canValidate = !!plan && ['proposed', 'validated'].includes(plan.state);
  const joint = plan?.rename_scope === 'video_subtitles';
  const needsRecovery = !!plan && ['applying', 'recovery_required'].includes(plan.state);
  const selectedRows = rows.filter(row => selected.includes(row.project_id));
  const scopeReady = selected.length > 0 && selected.every(id => inventory?.entries.some(entry => entry.project_id === id && ready(entry)));
  const completeScope = selected.length > 0 && selectedRows.length === selected.length;
  // Bind the displayed token to this exact draft; a late response can never authorize a newer edit.
  const draftKey = JSON.stringify([plan?.id, selected, selectedRows]);
  const canApply = visible && !working && !validating && canValidate && joint && scopeReady && completeScope
    && !!preview?.preview_token && previewFor === draftKey;

  useEffect(() => {
    if (working || !visible) return;
    if (plan?.adjusted_from && canValidate && focusedDraft.current !== plan.id) {
      container.current?.querySelector<HTMLInputElement>('.naming-row-title input')?.focus(); focusedDraft.current = plan.id;
    } else if (plan?.state === 'applied' && focusedDraft.current) {
      container.current?.querySelector<HTMLButtonElement>('.naming-adjust')?.focus(); focusedDraft.current = '';
    }
  }, [plan?.id, plan?.adjusted_from, plan?.state, canValidate, working, visible]);

  function own(identifier: string) {
    if (!inventoryRef.current?.entries.some(entry => entry.project_id === identifier)) throw new Error('这个命名任务不属于当前课程，未切换或修改文件。');
  }
  async function readInventory(signal: AbortSignal) {
    const next = await request<NamingInventory>(`/projects/${projectId}/naming`, signal);
    if (signal.aborted) return next;
    inventoryRef.current = next; setInventory(next);
    return next;
  }
  async function readPlan(identifier: string, signal: AbortSignal) {
    const next = await request<NamingPlan>(`/naming/plans/${identifier}`, signal);
    if (signal.aborted) return next;
    own(next.project_id); setPlan(next); setRestoreConfirm(false); setPreviewFor('');
    const edited = new Map((next.preview?.rows || []).map(row => [row.project_id, row]));
    setRows(next.rows.map(row => {
      const value = edited.get(row.project_id) || row;
      const single = inventoryRef.current?.entries.find(entry => entry.project_id === row.project_id)?.single_episode;
      return single && !['applied', 'rolled_back'].includes(next.state) ? { ...value, number: '' } : value;
    }));
    setSelected(next.preview?.rows.map(row => row.project_id) || next.rows.map(row => row.project_id));
    setPreview(next.preview ? { ...next.preview, plan_id: next.id, preview_token: next.preview_token || '' } : null);
    return next;
  }
  async function follow(initial: NamingJob, signal: AbortSignal) {
    own(initial.project_id);
    if (initial.operation.startsWith('folder_')) { setFolderTask(initial.id); return; }
    setJob(initial);
    const final = isActive(initial.state) ? await pollJob<NamingJob>(`/naming/tasks/${initial.id}`, signal, value => setJob(value)) : initial;
    if (signal.aborted) return;
    setJob(final); setCancelling(false);
    if (final.result?.plan_id) await readPlan(final.result.plan_id, signal);
    if (final.operation !== 'plan') await onChanged();
    await readInventory(signal);
    if (final.state !== 'completed') setError(final.message);
  }
  async function perform(action: (signal: AbortSignal) => Promise<unknown>) {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller;
    setPending(true); setError('');
    try { await action(controller.signal); }
    catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '操作未完成，未自动重试。'); }
    finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setPending(false); } }
  }
  useEffect(() => {
    setPlan(null); setRows([]); setPreview(null); setPreviewFor(''); setJob(null);
    void perform(async signal => {
      const next = await readInventory(signal);
      if (signal.aborted) return;
      setSelected(next.entries.filter(ready).map(entry => entry.project_id));
      if (initialTaskId) await follow(await request<NamingJob>(`/naming/tasks/${initialTaskId}`, signal), signal);
    });
    return () => { active.current?.abort(); active.current = null; cancelRequest.current?.abort(); cancelRequest.current = null; };
  }, [projectId, initialTaskId]);

  // Navigation preserves drafts and polling, but a returning draft must be checked again.
  useEffect(() => {
    if (!visible) { setPreviewFor(''); setRestoreConfirm(false); return; }
    if (!inventoryRef.current) return;
    const controller = new AbortController();
    void readInventory(controller.signal).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取项目列表。'); });
    return () => controller.abort();
  }, [visible]);

  // This endpoint only validates local filenames. Never invoke the model or apply from an effect.
  useEffect(() => {
    setValidationError(''); setValidating(false);
    if (!plan || !canValidate || !joint || working || !visible) return;
    setPreview(null); setPreviewFor('');
    if (!scopeReady || !completeScope) return;
    const controller = new AbortController();
    setValidating(true);
    const timer = window.setTimeout(() => {
      void request<NamingPreview>(`/naming/plans/${plan.id}/preview`, controller.signal, { rows: selectedRows })
        .then(result => {
          if (controller.signal.aborted) return;
          setPreview(result); setPreviewFor(draftKey);
          setPlan(current => current?.id === plan.id ? { ...current, state: 'validated' } : current);
        })
        .catch(reason => { if (!controller.signal.aborted) setValidationError(reason instanceof Error ? reason.message : '文件名校验失败，请修改后重试。'); })
        .finally(() => { if (!controller.signal.aborted) setValidating(false); });
    }, 350);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [draftKey, canValidate, joint, scopeReady, completeScope, working, visible, validationAttempt]);

  function toggle(identifier: string) {
    setSelected(values => values.includes(identifier) ? values.filter(value => value !== identifier) : [...values, identifier]);
    setPreview(null); setPreviewFor('');
  }
  function edit(identifier: string, key: 'number' | 'title', value: string) {
    setRows(values => values.map(row => row.project_id === identifier ? { ...row, [key]: value } : row));
    setPreview(null); setPreviewFor('');
  }
  async function generate(signal: AbortSignal) {
    setPlan(null); setRows([]); setPreview(null); setPreviewFor(''); setJob(null); setRestoreConfirm(false);
    await follow(await request<NamingJob>(`/projects/${projectId}/naming/plan`, signal,
      { consent: true, agent: models.selected, selected_ids: selected }), signal);
  }
  async function adjustNaming(signal: AbortSignal) {
    if (!plan || plan.state !== 'applied') return;
    const result = await request<{ plan_id: string }>(`/projects/${projectId}/naming/adjust`, signal, { plan_id: plan.id });
    if (signal.aborted) return;
    await readInventory(signal);
    if (signal.aborted) return;
    await readPlan(result.plan_id, signal);
    if (!signal.aborted) setJob(null);
  }
  async function cancelAdjustment(signal: AbortSignal) {
    if (!plan?.adjusted_from) return;
    await readInventory(signal);
    if (signal.aborted) return;
    await readPlan(plan.adjusted_from, signal);
    if (!signal.aborted) setJob(null);
  }
  async function cancel() {
    if (!job || !isActive(job.state) || job.phase === 'archiving' || cancelling) return;
    const controller = new AbortController(); cancelRequest.current = controller; setCancelling(true);
    try { await cancelTask('naming', job.id, controller.signal); }
    catch (reason) { if (!controller.signal.aborted) { setCancelling(false); setError(reason instanceof Error ? reason.message : '取消请求未完成。'); } }
    finally { if (cancelRequest.current === controller) cancelRequest.current = null; }
  }
  const displayEntries = plan?.entries || inventory?.entries || [];
  // Completed results belong to the persisted operation, not the next selection's draft preview.
  const resultRows = plan?.state === 'applied' ? plan.preview?.rows || rows : rows;
  return <>
    {/* One model choice for the whole view: folder naming and subtitle naming both read it. */}
    <section className="naming-model-bar" aria-labelledby="naming-model-bar-title">
      <h2 id="naming-model-bar-title" className="naming-model-bar-title">选择模型</h2>
      <AgentModelPicker state={models} disabled={working || needsRecovery || folderBusy} onManage={onOpenSettings} />
    </section>
    <ProjectFolderNaming projectId={projectId} models={models} onBusyChange={setFolderBusy} visible={visible} initialTaskId={folderTask} onChanged={onChanged} onBeforeApply={onBeforeApply} />
    <section ref={container} className="subtitle-naming-panel" aria-labelledby="subtitle-naming-title" aria-busy={working}>
    <header className="subtitle-naming-heading"><h2 id="subtitle-naming-title">文件重命名</h2>
      <p className="sr-only">先生成新名字，确认后再同步修改视频与字幕。</p></header>
    {inventory ? <>
      <section className="naming-scope" aria-labelledby="naming-scope-title">
        <div className="naming-section-heading"><h3 id="naming-scope-title">选择分集</h3><span className="naming-hint">已选 {selected.length} 集</span></div>
        <ul>{inventory.entries.map(entry => <li key={entry.project_id}><label><input type="checkbox" checked={selected.includes(entry.project_id)}
          disabled={working || needsRecovery || (!ready(entry) && !selected.includes(entry.project_id))} onChange={() => toggle(entry.project_id)} />
          <span><span translate="no">{entry.part ? `P${entry.part} · ` : ''}{entry.title || entry.files[0]?.filename}</span>
            {!ready(entry) && <small>{!entry.files.length ? '未归档视频' : '缺少字幕，请先生成字幕'}</small>}</span></label></li>)}</ul>
        {!inventory.entries.length && <p className="naming-hint">暂无可命名的分集。</p>}
        {selected.length > inventory.max_selected && <p className="notice">一次最多选择 {inventory.max_selected} 集。</p>}
      </section>
      <div className="naming-generate">
        <p className="sr-only">{inventory.entries.every(entry => entry.single_episode) ? '单集视频只使用名称，不添加层级序号。' : '逐集参考前序文件名和当前集字幕，延续语义章节编号。'}</p>
        <p className="sr-only" id="naming-model-consent">点击生成即同意由所选模型处理字幕，可能联网并产生费用；不会自动重试。</p>
        <button type="button" className="primary" aria-describedby="naming-model-consent"
          disabled={working || needsRecovery || !models.configured || !scopeReady || selected.length > inventory.max_selected}
          onClick={() => { void perform(generate); }}>{plan ? '重新生成新名字' : '生成新名字'}</button>
      </div>
    </> : <p role="status">{error && !pending ? '无法读取分集，请刷新页面重试。' : '正在读取分集…'}</p>}
    {job && isActive(job.state) && <section className="naming-job" aria-label="命名任务状态"><p role="status">{job.message}{job.progress?.total ? ` ${job.progress.done || 0} / ${job.progress.total}` : ''}</p>
      <button type="button" disabled={cancelling || job.phase === 'archiving'} onClick={() => { void cancel(); }}>{cancelling ? '已请求取消' : '取消任务'}</button>
      {!pending && <button type="button" onClick={() => { void perform(async signal => follow(await request<NamingJob>(`/naming/tasks/${job.id}`, signal), signal)); }}>重新读取任务</button>}
    </section>}
    {plan && <section className="naming-editor" aria-label="新名字与确认">
      <div className="naming-section-heading"><h3>{plan.state === 'failed' && rows.length ? '生成中断，已有建议仅供参考' : states[plan.state] || plan.state}</h3></div>
      {canValidate && !joint && <p className="notice">这是旧版仅视频预览，请重新生成新名字，以包含字幕。</p>}
      {plan.excerpted && <p className="naming-hint">部分字幕因上下文预算使用了选段，请检查建议是否准确。</p>}
      {plan.last_error && <p className="error-text">{plan.last_error}</p>}
      {canValidate && <>
        <p className="naming-hint">可以直接编辑以下新名字。确认前不会修改文件。</p>
        {plan.adjusted_from && <p className="naming-hint">直接编辑上次结果，不会重新调用模型。</p>}
        <ul className={`naming-rows${plan.source === 'subtitle' ? ' is-subtitle' : ''}`}>{selectedRows.map(row => {
          const entry = displayEntries.find(item => item.project_id === row.project_id);
          const part = entry?.part ? `P${entry.part}` : '视频';
          return <li key={row.project_id}>
            <div className="naming-row-context"><strong>{part}</strong><span translate="no">{entry?.title}</span></div>
            <div className="naming-row-fields">
              {plan.source !== 'subtitle' && !inventory?.entries.find(item => item.project_id === row.project_id)?.single_episode && <label><span>层级序号</span><input aria-label={`${part} 层级序号`} value={row.number} maxLength={60}
                placeholder={plan.source === 'manual' ? '可留空' : undefined} disabled={working} onChange={event => edit(row.project_id, 'number', event.target.value)} /></label>}
              <label className="naming-row-title"><span>新名称</span><input aria-label={`${part} 新名称`} value={row.title} maxLength={100}
                disabled={working || plan.source === 'subtitle'} onChange={event => edit(row.project_id, 'title', event.target.value)} /></label>
            </div>
          </li>;
        })}</ul>
        {selected.length === 0 && <p className="notice">请至少选择一集。</p>}
        {selected.length > 0 && !completeScope && <p className="notice">分集选择已变化，请重新生成新名字。</p>}
        {validating && <p className="naming-hint" role="status">正在校验文件名…</p>}
        {validationError && <div><p className="error-text" role="alert">{validationError}</p>
          <button type="button" disabled={working} onClick={() => setValidationAttempt(value => value + 1)}>重新校验文件名</button></div>}
        {preview && previewFor === draftKey && <section className="naming-final-preview" aria-label="真实文件改名预览">
          <h4>将修改以下文件</h4><ol>{preview.moves.map(move => <li key={`${move.project_id}:${move.resource}`}>
            <span className="naming-move-part">{displayEntries.find(entry => entry.project_id === move.project_id)?.part ? `P${displayEntries.find(entry => entry.project_id === move.project_id)!.part}` : '视频'}</span>
            <code>{move.source}</code><span aria-hidden="true">→</span><code>{move.target}</code>
          </li>)}</ol>
          {!!preview.directory_moves?.length && <><h4>同步调整文件夹</h4><ol>{preview.directory_moves.map(move => <li key={move.project_id}>
            <code>{move.old}</code><span aria-hidden="true">→</span><code>{move.new}</code>
          </li>)}</ol></>}
        </section>}
        <div className="naming-confirm">
          <p className="naming-hint">确认后，视频、字幕与分集文件夹一起更新；学习关联保持不变。请先关闭占用文件的外部程序。</p>
          <div className="naming-actions"><button type="button" className="primary" disabled={!canApply} onClick={() => {
            if (!canApply || !preview) return;
            onBeforeApply(); setPreviewFor('');
            void perform(async signal => follow(await request<NamingJob>(`/naming/plans/${plan.id}/apply`, signal,
              { confirm: true, preview_token: preview.preview_token }), signal));
          }}>确认并重命名</button>
            {plan.adjusted_from && <button type="button" disabled={working} onClick={() => { void perform(cancelAdjustment); }}>取消调整</button>}
          </div>
        </div>
      </>}
      {!canValidate && rows.length > 0 && <ul className="naming-results" aria-label="命名结果">{resultRows.map(row => {
        const entry = displayEntries.find(item => item.project_id === row.project_id);
        const original = plan.state === 'rolled_back';
        const originalTitle = entry?.number && entry.title.startsWith(`${entry.number} `) ? entry.title.slice(entry.number.length + 1) : entry?.title;
        return <li key={row.project_id}><span className="naming-result-part">{entry?.part ? `P${entry.part}` : '视频'}</span><span className="naming-result-number" translate="no">{original ? entry?.number : row.number}</span><span translate="no">{original ? originalTitle : row.title}</span></li>;
      })}</ul>}
      {plan.state === 'applied' && <button type="button" className="naming-adjust" disabled={working} onClick={() => { void perform(adjustNaming); }}>调整命名</button>}
      {needsRecovery && <div className="naming-recovery">
        <p className="notice">上次文件改名未完成，已暂停新的命名，请先恢复未完成的操作。</p>
        <label><input type="checkbox" checked={restoreConfirm} disabled={working} onChange={event => setRestoreConfirm(event.target.checked)} />确认恢复未完成操作的原文件名</label>
        <button type="button" disabled={working || !restoreConfirm} onClick={() => {
          if (!restoreConfirm) return;
          onBeforeApply(); setRestoreConfirm(false);
          void perform(async signal => follow(await request<NamingJob>(`/naming/plans/${plan.id}/restore`, signal, { confirm: true }), signal));
        }}>恢复未完成操作</button>
      </div>}
    </section>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </section></>;
}
