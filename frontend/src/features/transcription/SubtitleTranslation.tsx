import MathTextArea from '../../components/MathTextArea';
import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { ArchivedTranslation, Configuration, TranscriptionJob } from '../../lib/api';
import { cancelTask, isActive, pollJob, remember } from '../../lib/jobs';
import SavedLocation from './SavedLocation';
import AgentModelPicker from '../learning/AgentModelPicker';
import useAgentModels from '../learning/useAgentModels';

type Source = { job_id: string } | { project_id: string; resource: string; video_resource?: string };
// restoredJob: a translation of this video restored after a reload / from the task center, shown here
// (where it belongs) instead of in the transcription result area.
type Props = { initialOpen?: boolean; onOpenChange?: (open: boolean) => void; unified?: boolean; onCompleted?: (job: TranscriptionJob) => void; source: Source; text: string; language?: string; config?: Configuration | null; label?: string; textId?: string; rows?: number; restoredJob?: TranscriptionJob | null; archivedTranslations?: ArchivedTranslation[]; onOpenSettings?: (button: HTMLButtonElement) => void };
export default function SubtitleTranslation({ initialOpen = false, onOpenChange, unified = false, onCompleted, source, text, language, config, label, textId, rows = 8, restoredJob, archivedTranslations = [], onOpenSettings }: Props) {
  const [open, setOpen] = useState(initialOpen);
  useEffect(() => { onOpenChange?.(open); }, [open, onOpenChange]);
  const [target, setTarget] = useState<'zh' | 'en'>(() => language === 'en' ? 'zh' : language === 'zh' ? 'en' : (text.match(/[\u3400-\u9fff]/g)?.length || 0) > (text.match(/[a-zA-Z]/g)?.length || 0) / 3 ? 'en' : 'zh');
  const [outputConfirmed, setOutputConfirmed] = useState(false);
  const [job, setJob] = useState<TranscriptionJob | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<AbortController | null>(null);
  const models = useAgentModels(config?.ai || null, !!config?.ai_configured);
  // A new run replaces the restored result instead of showing it while the request is pending.
  const [freshRun, setFreshRun] = useState(false);
  useEffect(() => {
    if (!restoredJob) return;
    active.current?.abort(); active.current = null;
    setPending(false); setJob(null); setFreshRun(false); setError('');
  }, [restoredJob?.id]);
  const current = job || (freshRun ? null : restoredJob) || null;
  const [translationResource, setTranslationResource] = useState('');
  const completed = current?.state === 'completed' ? current : null;
  // One result picker for archived files AND a just-finished/restored task, even with one choice.
  const choices = archivedTranslations.map(item => ({ key: item.resource, filename: item.filename, text: item.text,
    location: item.location || item.resource, openPath: 'project_id' in source ? `/projects/${source.project_id}/open` : '', body: { folder: 'subtitles' } as Record<string, string> }));
  const matching = completed && choices.find(item => item.filename === completed.files[0]?.name);
  if (completed && matching) {
    matching.text = completed.text; matching.location = completed.saved_to?.location || matching.location;
    matching.openPath = `/transcriptions/${completed.id}/open`; matching.body = {};
  }
  if (completed && !matching) choices.unshift({ key: 'task:' + completed.id, filename: completed.files[0]?.name || completed.title,
    text: completed.text, location: completed.saved_to?.location || '', openPath: `/transcriptions/${completed.id}/open`, body: {} });
  const saved = choices.find(item => item.key === translationResource) || matching || choices[0];
  const completedId = job?.state === 'completed' ? job.id : '';
  useEffect(() => { if (completedId && job) { onCompleted?.(job); window.dispatchEvent(new Event('video-parser:subtitles-changed')); } }, [completedId]);
  const working = pending || isActive(current?.state);
  const local = !models.native && models.apiSettings?.provider === 'llamacpp';
  // Keep the user's batch preference when switching models; local models still use their own limits.
  useEffect(() => () => active.current?.abort(), []);
  async function start() {
    if (active.current || working || !models.configured) return;
    const controller = new AbortController(); active.current = controller;
    setPending(true); setError(''); setJob(null); setFreshRun(true); setTranslationResource('');
    try {
      // Clicking Start explicitly submits this translation; opening or changing models never submits.
      const result = await request<TranscriptionJob>('/translations', controller.signal, { ...source, target, consent: true, agent: models.selected, output_confirmed: !local && outputConfirmed });
      if (controller.signal.aborted) return;
      remember('transcriptions', result.id); setJob(result);
      if (isActive(result.state)) await pollJob<TranscriptionJob>('/transcriptions/' + result.id, controller.signal, value => setJob(value));
    } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '翻译未完成，原字幕保留。'); }
    finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setPending(false); } }
  }
  return <section className="subtitle-translation" aria-label="AI 中英互译">
    <div className="translation-heading">
      {label && <span>{label}</span>}
      <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>AI 中英互译</button>
    </div>
    {label && <MathTextArea id={textId} aria-label={label} rows={rows} value={text} />}
    {open && <>
      <div className="translation-controls"><label>翻译方向<select aria-label="翻译方向" value={target} disabled={working} onChange={event => setTarget(event.target.value as 'zh' | 'en')}>
        <option value="zh">英文 → 中文</option><option value="en">中文 → 英文</option>
      </select></label></div>
      <AgentModelPicker state={models} disabled={working} onManage={button => { if (onOpenSettings) onOpenSettings(button); else setError('请在设置中配置 AI 模型。'); }} />
      <p className="subtitle-note">翻译仅发送当前字幕文字，不发送音视频。长字幕会分批请求，可能产生多次费用；失败不自动重试。原字幕保留，时间戳不变，译文单独保存。</p>
      {local ? <p className="subtitle-note">本机模型按启动配置的上下文和单次回复上限自动分批。</p>
        : <label className="naming-consent translation-batch-option"><input type="checkbox" checked={outputConfirmed} disabled={working} onChange={event => setOutputConfirmed(event.target.checked)} />
          <span>我已确认所选模型及接入端单次输出不少于 32K tokens，启用云端大批次（每批最多 20,000 字符／300 条）。不勾选则按兼容档（2,200 字符／24 条）分批。</span></label>}
      <div className="translation-controls"><button type="button" className="primary" disabled={working || !models.configured || !text.trim()} onClick={() => { void start(); }}>开始 AI 翻译</button>
        {current && isActive(current.state) && <button type="button" onClick={() => { void cancelTask('transcriptions', current.id, new AbortController().signal).catch(() => setError('取消请求未完成，请在任务中心检查。')); }}>取消翻译</button>}</div>
    </>}
    {/* Status and result stay visible when the settings above are folded. */}
    {/* Progress or failure only: a finished translation speaks for itself (译文 + where it was saved). */}
    {current && current.state !== 'completed' && <p className="translation-status" role="status">{current.message}</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {saved && !unified && <div className="translation-result">
      <label>已保存译文<select aria-label="已保存译文" value={saved.key} onChange={e => setTranslationResource(e.target.value)}>{choices.map(item => <option key={item.key} value={item.key}>{item.filename}</option>)}</select></label>
      <div><span>译文</span><MathTextArea aria-label="译文" rows={8} value={saved.text} /></div>
      {saved.location && <SavedLocation label="译文已保存到" location={saved.location} openPath={saved.openPath} body={saved.body} />}
    </div>}
    {unified && completed && <p className="subtitle-note">翻译已完成，结果已加入当前视图。</p>}
    {working && <p className="subtitle-note">切换视频或离开页面不会取消后台翻译，可在任务中心继续查看。</p>}
  </section>;
}
