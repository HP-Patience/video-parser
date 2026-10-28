import { useEffect, useMemo, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { AgentTarget, AISettings, AnalysisBody, AnalysisContext, AnalysisFrame, AnalysisRecord, AnalysisSource, Configuration, Media, ParsedVideo } from '../../lib/api';
import { phases } from '../../lib/jobs';
import type { TaskInfo } from '../../lib/jobs';
import Icon from '../../components/Icon';
import Panel, { PanelTitle } from '../../components/Panel';
import Markdown from '../../components/Markdown';
import FrameLightbox from '../../components/FrameLightbox';
import './analysis.css';
import { extractChapters } from './chapters';
import type { AnalysisTimeline } from './chapters';
import type { LearningChat } from '../learning/AIChat';
import useConversations from '../learning/useConversations';
import useAgentModels from '../learning/useAgentModels';
import AgentModelPicker from '../learning/AgentModelPicker';
import QuizView from './Quiz';
import { chapterMaterial, matchesLearningMedia, readChapterChoice, readMaterialChoice, saveChapterChoice, saveMaterialChoice } from './learningMaterials';

type Props = {
  learning: boolean; onManage: () => void; onPrepare: () => void; onUseForLearning: () => void;
  parsed: ParsedVideo | null; media: Media | null; qualityId: string; config: Configuration | null;
  busy: 'prepare' | 'analyze' | null; status: string; error: boolean; task?: TaskInfo;
  version: number; lastRecord?: string; cancelDisabled: boolean;
  onCancel: () => void; onAnalyze: (body: AnalysisBody) => void;
  onAsk: (conversationId: string, question: string, position: number, agent?: AgentTarget) => void; onSeek?: (seconds: number) => void;
  onTimelineChange: (timeline: AnalysisTimeline | null) => void; onChatChange?: (chat: LearningChat | null) => void;
  onOpenSettings: (button: HTMLButtonElement) => void; onGenerateSubtitles: () => void;
};
type Preview = { token: string; frames: (AnalysisFrame & { name: string })[] };

const SOURCES: [AnalysisSource, string][] = [['both', '字幕 + 画面'], ['transcript', '只用字幕'], ['frames', '只用画面']];
const COUNTS = [4, 6, 8, 12, 16];
const PLACEHOLDERS: Record<string, string> = {
  custom: '写下你想让模型做什么，例如：列出视频里出现的所有产品和价格',
  notes: '例如：只整理和特征值有关的部分',
  quiz: '例如：只考计算题，出 8 道，难度中等',
  breakdown: '例如：重点分析前 10 秒',
};
const autoCount = (duration: number) => Math.max(4, Math.min(16, Math.round(4 + duration / 60)));
const tokens = (value: number) => value >= 10000 ? `${(value / 10000).toFixed(1)} 万` : value.toLocaleString('zh-CN');
function when(iso: string | null) {
  if (!iso) return '时间未知';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '时间未知';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function hostOf(url: string) { try { return new URL(url).host; } catch { return url; } }

export default function AnalysisPanel(props: Props) {
  const { learning, parsed, media, qualityId, config, busy, status, error, task, version, lastRecord, onSeek } = props;
  const reading = useRef(learning); reading.current = learning;
  const projectId = parsed?.project.id || '';
  const resource = media?.resource || '';
  const [context, setContext] = useState<AnalysisContext | null>(null);
  const [loadError, setLoadError] = useState('');
  const [records, setRecords] = useState<AnalysisRecord[]>([]);
  const [recordsFor, setRecordsFor] = useState({ project: '', resource: '' });
  const [active, setActive] = useState<string | null>(null);
  const [learningRecord, setLearningRecord] = useState<string | null>(null);
  const [chapterChoice, setChapterChoice] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [template, setTemplate] = useState('summary');
  const [source, setSource] = useState<AnalysisSource>('both');
  const [transcript, setTranscript] = useState('');
  const [frameMode, setFrameMode] = useState<'scene' | 'uniform' | 'ai'>('scene');
  const [count, setCount] = useState(0);
  const [prompt, setPrompt] = useState('');
  const [consent, setConsent] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [kept, setKept] = useState<Set<string>>(new Set());
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [largeFrame, setLargeFrame] = useState<AnalysisFrame | null>(null);
  useEffect(() => setLargeFrame(null), [projectId, resource]);
  const [notice, setNotice] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [configExpanded, setConfigExpanded] = useState<boolean | null>(null);
  useEffect(() => { setConfigExpanded(null); }, [projectId, resource]);
  const previewRequest = useRef<AbortController | null>(null);
  const shown = useRef({ project: '', resource: '', version: 0 });
  // Model settings: from the latest context load, or from the settings page when it saves.
  const [ai, setAI] = useState<AISettings | null>(null);
  useEffect(() => { if (context?.ai) setAI(context.ai); }, [context]);
  useEffect(() => { if (config?.ai) setAI(config.ai); }, [config?.ai]);

  useEffect(() => {
    setConsent(false); setNotice(''); setConfirmDelete(false);
    if (!projectId) { setContext(null); setRecords([]); setActive(null); return; }
    const controller = new AbortController();
    setLoadError('');
    Promise.all([
      request<AnalysisContext>(`/projects/${projectId}/analysis/context?resource=${encodeURIComponent(resource)}`, controller.signal),
      request<{ records: AnalysisRecord[] }>(`/projects/${projectId}/analyses`, controller.signal),
    ]).then(([nextContext, list]) => {
      if (controller.signal.aborted) return;
      setRecordsFor({ project: projectId, resource });
      setContext(nextContext);
      setRecords(list.records);
      const preferred = readMaterialChoice(projectId, resource);
      const eligible = list.records.filter(item => matchesLearningMedia(item, media, parsed?.episodes?.length || 0));
      setLearningRecord(eligible.find(item => item.id === preferred)?.id || eligible[0]?.id || null);
      setChapterChoice(readChapterChoice(projectId, resource));
      // A just-finished analysis or follow-up: bring the result into view.
      if (!reading.current && shown.current.project === projectId && version > shown.current.version && lastRecord) requestAnimationFrame(() => {
        const panel = document.querySelector('.analysis-records');
        if (!reading.current && panel?.getClientRects().length) panel.scrollIntoView({ block: 'start', behavior: 'smooth' });
      });
      const sameVideo = shown.current.project === projectId && shown.current.resource === resource;
      shown.current = { project: projectId, resource, version };
      setActive(current => lastRecord && list.records.some(item => item.id === lastRecord) ? lastRecord
        : sameVideo && current && list.records.some(item => item.id === current) ? current
        : list.records.find(item => item.video_resource === resource)?.id || list.records[0]?.id || null);
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setLoadError(reason instanceof Error ? reason.message : '无法读取分析信息。'); });
    return () => controller.abort();
  }, [projectId, resource, version, lastRecord, reload]);
  // New frames are needed whenever the video or the way frames are picked changes.
  useEffect(() => { previewRequest.current?.abort(); setPreview(null); setKept(new Set()); setPreviewError(''); setPreviewing(false); }, [projectId, resource, frameMode, count]);
  useEffect(() => () => previewRequest.current?.abort(), []);
  useEffect(() => { setConfirmDelete(false); setNotice(''); }, [active]);
  useEffect(() => { setConfirmDelete(false); }, [learning]);
  useEffect(() => {
    const refresh = () => setReload(value => value + 1);
    window.addEventListener('video-parser:subtitles-changed', refresh);
    return () => window.removeEventListener('video-parser:subtitles-changed', refresh);
  }, []);

  const models = useAgentModels(ai, !!config?.ai_configured);
  const chatModels = useAgentModels(ai, !!config?.ai_configured);
  const configured = models.configured;
  const needsConsent = models.native || (!!ai && !ai.consented);
  useEffect(() => { setConsent(false); }, [models.key]);
  const templates = context?.templates || [];
  const spec = templates.find(item => item.id === template);
  const transcripts = context?.transcripts || [];
  const chosen = transcripts.find(item => item.resource === transcript) || transcripts[0];
  const effective: AnalysisSource = source;
  const usesTranscript = effective !== 'frames';
  const usesFrames = effective !== 'transcript';
  const duration = context?.duration || media?.duration || parsed?.duration || 0;
  const automatic = autoCount(duration);
  const frameTotal = usesFrames ? (frameMode === 'ai' ? 16 : preview ? kept.size : count || automatic) : 0;
  const running = busy === 'analyze';
  const working = busy !== null;
  const recordsReady = recordsFor.project === projectId && recordsFor.resource === resource;
  const eligibleRecords = records.filter(item => matchesLearningMedia(item, media, parsed?.episodes?.length || 0));
  const visibleRecords = recordsReady ? (learning ? eligibleRecords : records) : [];
  const record = learning ? visibleRecords.find(item => item.id === learningRecord) || visibleRecords[0] || null
    : visibleRecords.find(item => item.id === active) || null;
  const recordMatches = !!record && matchesLearningMedia(record, media, parsed?.episodes?.length || 0);
  // Management is a material editor, not another player. Only the learning reader can seek.
  const recordSeek = recordMatches ? onSeek : undefined;
  function chooseLearningMaterial(id: string) {
    const chosenRecord = eligibleRecords.find(item => item.id === id);
    if (!chosenRecord) return;
    setLearningRecord(id); saveMaterialChoice(projectId, resource, id);
    if (extractChapters(chosenRecord.text, duration).length) {
      setChapterChoice(id); saveChapterChoice(projectId, resource, id);
    }
  }
  function useForLearning() {
    if (!record || !recordMatches) return;
    chooseLearningMaterial(record.id); props.onUseForLearning();
  }
  const timeline = useMemo(() => recordsReady ? chapterMaterial(records, media, parsed?.episodes?.length || 0, duration, chapterChoice) : null,
    [recordsReady, records, media, parsed?.episodes?.length, duration, chapterChoice]);
  useEffect(() => {
    props.onTimelineChange(timeline ? { projectId, videoResource: timeline.record.video_resource,
      recordId: timeline.record.id, label: timeline.record.template_name, chapters: timeline.chapters } : null);
  }, [timeline, projectId, props.onTimelineChange]);
  const conversation = useConversations(projectId, resource, version, working);
  const provider = chatModels.name, host = !chatModels.native && ai ? hostOf(ai.base_url) : '';
  const chatConsent = chatModels.native || (!!ai && !ai.consented);
  const chat = useMemo<LearningChat>(() => ({ ...conversation, configured: chatModels.configured, needsConsent: chatConsent, provider, host, models: chatModels }),
    [conversation, chatConsent, provider, host, chatModels]);
  const shownConversation = useRef('');
  useEffect(() => {
    const session = conversation.session;
    const key = projectId + ':' + (session?.id || '');
    if (!session || shownConversation.current === key) return;
    shownConversation.current = key;
    // Restoring a conversation must not override the model for NEW learning-material analysis.
    chatModels.select(session.agent || { engine: 'api', model: '', effort: '' });
  }, [projectId, conversation.session, chatModels.select]);
  useEffect(() => { props.onChatChange?.(projectId ? chat : null); }, [projectId, chat, props.onChatChange]);


  const inputTokens = (context?.prompt_tokens || 700) + (usesTranscript && chosen ? chosen.tokens : 0) + frameTotal * (context?.image_tokens || 800) + Math.ceil(prompt.length * 0.7);
  const outputTokens = context?.output_tokens || 1500;
  const cost = ai && ai.price_input !== null ? inputTokens / 1e6 * ai.price_input + outputTokens / 1e6 * (ai.price_output ?? ai.price_input) : null;
  // Historical records keep their token counts; the price is the one currently configured, so label it as an estimate.
  const recordCost = record && ai && record.engine === 'api' && !ai.local && ai.price_input !== null && record.usage.prompt_tokens
    ? record.usage.prompt_tokens / 1e6 * ai.price_input + (record.usage.completion_tokens || 0) / 1e6 * (ai.price_output ?? ai.price_input)
    : null;

  const missingTranscript = !transcripts.length && (source !== 'frames' || spec?.requires === 'transcript' || frameMode === 'ai');
  const blocker = !parsed ? '先解析链接并选择视频。'
    : !context ? (loadError || '正在读取分析信息…')
    : missingTranscript ? '这个视频还没有字幕，先生成字幕再分析。'
    : template === 'custom' && !prompt.trim() ? '自定义分析需要写下分析要求。'
    : usesFrames && preview && !kept.size ? '至少保留一张画面。'
    : !configured ? (models.native ? '未找到所选本地 agent，请检查安装后刷新。' : '尚未配置 AI 模型。')
    : needsConsent && !consent ? '请先勾选同意发送。' : '';

  function chooseTemplate(id: string) {
    setTemplate(id);
    const next = templates.find(item => item.id === id);
    if (next) setSource(next.source);
  }
  function body(): AnalysisBody {
    return {
      agent: models.selected, template, source: effective, transcript_resource: usesTranscript || frameMode === 'ai' ? chosen?.resource || null : null, frame_mode: frameMode,
      count: usesFrames && frameMode !== 'ai' ? count : 0, preview_token: usesFrames && preview ? preview.token : null,
      frames: usesFrames && preview ? preview.frames.filter(frame => kept.has(frame.name)).map(frame => frame.name) : null, prompt: prompt.trim(),
    };
  }
  async function loadPreview() {
    if (!parsed || !media || previewing) return;
    previewRequest.current?.abort();
    const controller = new AbortController(); previewRequest.current = controller;
    setPreviewing(true); setPreviewError('');
    try {
      const result = await request<Preview>(`/videos/${parsed.id}/analysis/frames`, controller.signal, { quality_id: qualityId, mode: frameMode, count });
      if (controller.signal.aborted) return;
      setPreview(result); setKept(new Set(result.frames.map(frame => frame.name)));
    } catch (reason) {
      if (!controller.signal.aborted) setPreviewError(reason instanceof Error ? reason.message : '截取画面失败。');
    } finally { if (previewRequest.current === controller) { previewRequest.current = null; setPreviewing(false); } }
  }
  function toggleFrame(name: string) {
    setKept(current => { const next = new Set(current); if (next.has(name)) next.delete(name); else next.add(name); return next; });
  }
  async function copyRecord() {
    if (!record) return;
    const text = [record.text.trim(), ...record.followups.map(item => `## 追问：${item.question}\n\n${item.answer.trim()}`)].join('\n\n');
    try { await navigator.clipboard.writeText(text); setNotice('已复制到剪贴板。'); }
    catch {
      const area = document.createElement('textarea'); area.value = text; area.setAttribute('readonly', ''); area.style.position = 'fixed'; area.style.opacity = '0';
      document.body.append(area); area.select();
      const copied = document.execCommand('copy'); area.remove();
      setNotice(copied ? '已复制到剪贴板。' : '浏览器不允许复制，请导出 Markdown。');
    }
  }
  async function deleteRecord() {
    if (!record || !projectId) return;
    try {
      const result = await request<{ records: AnalysisRecord[] }>(`/projects/${projectId}/analyses/${record.id}/delete`, new AbortController().signal, { confirm: true });
      setRecords(result.records); setActive(result.records[0]?.id || null);
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : '删除失败，请重试。'); setConfirmDelete(false); }
  }


  const estimate = !parsed || !context ? '' : `预计发送约 ${tokens(inputTokens)} tokens`
    + `（${[usesTranscript && chosen ? `字幕约 ${tokens(chosen.tokens)}` : '', usesFrames ? `画面 ${frameTotal} 张` : ''].filter(Boolean).join(' · ')}），输出约 ${tokens(outputTokens)}`;
  const price = models.native ? '费用由所选 agent 的模型服务计算。' : !ai ? '' : ai.local ? '本机模型，不产生调用费用。' : cost !== null ? `按设置的单价约 ¥${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)}。` : '未填写单价，无法估算费用（可在设置 → AI 模型填写）。';
  const phase = task?.phase && phases[task.phase] ? phases[task.phase] + ' · ' : '';

  // Never hide progress, cancellation or errors. A manual disclosure choice survives refreshes.
  const configRequired = working || previewing || !!loadError || !!previewError || !!error;
  const configOpen = configRequired || (configExpanded ?? visibleRecords.length === 0);
  if (!parsed) return <Panel className={learning ? 'learning-materials' : 'analysis-form'}>
    <PanelTitle>{learning ? '还没有选择学习视频' : 'AI 学习材料管理'}</PanelTitle>
    <p className="analysis-empty">先在视频工作台解析并归档视频，再开始学习或制作 AI 学习材料。</p>
    <button type="button" onClick={props.onPrepare}>去准备视频</button></Panel>;

  return <>
    <FrameLightbox frame={largeFrame} onClose={() => setLargeFrame(null)} onSeek={recordSeek} />
    <Panel className="analysis-form" hidden={learning} aria-labelledby="analysis-title">
      <div className="analysis-config-heading">
        <div><PanelTitle id="analysis-title">生成 AI 学习材料</PanelTitle>
          {!configOpen && <p className="analysis-hint">展开配置以生成新材料；已有结果在下方。</p>}</div>
        {visibleRecords.length > 0 && <button type="button" aria-controls="analysis-configuration" aria-expanded={configOpen}
          disabled={configRequired} onClick={() => setConfigExpanded(!configOpen)}>{configOpen ? '收起生成配置' : '生成新材料'}</button>}
      </div>
      <div id="analysis-configuration" className="analysis-configuration" hidden={!configOpen} onFocusCapture={() => setConfigExpanded(true)}>
      {loadError && <p className="error-text" role="alert">{loadError}<button type="button" onClick={() => setReload(value => value+1)}>重新载入材料</button></p>}
      {!media && <p className="notice">请先归档所选视频，再生成学习材料。<button type="button" className="link-button" onClick={props.onPrepare}>去准备视频</button></p>}
      <AgentModelPicker state={models} disabled={working} onManage={props.onOpenSettings} />
      {!configured && !models.native && <p className="notice analysis-setup" role="note">尚未配置 AI 模型，填写密钥后无需重启。
        <button type="button" className="link-button" onClick={event => props.onOpenSettings(event.currentTarget)}>打开设置</button></p>}

      <div className="analysis-block">
        <span className="analysis-label" id="analysis-template-label">分析模板</span>
        <div className="analysis-templates" role="group" aria-labelledby="analysis-template-label">
          {templates.map(item => <button key={item.id} type="button" className="chip" aria-pressed={template === item.id} disabled={running} onClick={() => chooseTemplate(item.id)}>{item.name}</button>)}
        </div>
        {spec && <p className="analysis-hint">{spec.description}</p>}
      </div>

      <fieldset className="analysis-block analysis-source" disabled={running}>
        <legend className="analysis-label">分析依据</legend>
        <div className="analysis-segment">
          {SOURCES.map(([value, label]) => <label key={value} className={source === value ? 'checked' : ''}>
            <input type="radio" name="analysis-source" value={value} checked={source === value}
              disabled={(value === 'frames' && spec?.requires === 'transcript') || (value === 'transcript' && spec?.requires === 'frames')}
              onChange={() => setSource(value)} />{label}</label>)}
        </div>
        {usesTranscript && transcripts.length > 1 && <label className="analysis-inline">
          <span>字幕</span>
          <select value={chosen?.resource || ''} onChange={event => setTranscript(event.target.value)} aria-label="使用哪份字幕">
            {transcripts.map(item => <option key={item.resource} value={item.resource}>{item.label} · {item.cues} 条 · 到 {item.end}</option>)}
          </select>
        </label>}
        {usesTranscript && transcripts.length === 1 && chosen && <p className="analysis-hint">使用{chosen.label}：{chosen.cues} 条，到 {chosen.end}{chosen.merged ? '（较长，会按时间段合并）' : ''}。</p>}
        {!transcripts.length && context && (source !== 'frames' || frameMode === 'ai') && <p className={'analysis-hint' + (missingTranscript ? ' analysis-warning' : '')}>
          {'当前视频暂无可用字幕，请先获取平台字幕或生成转写字幕；不会自动改为只用画面。'}
          <button type="button" className="link-button" onClick={props.onGenerateSubtitles}>先生成字幕</button></p>}
      </fieldset>

      {usesFrames && <div className="analysis-block">
        <span className="analysis-label">画面</span>
        <div className="analysis-frame-controls">
          <select aria-label="选取画面的方式" value={frameMode} disabled={running} onChange={event => setFrameMode(event.target.value as 'scene' | 'uniform' | 'ai')}>
            <option value="ai">AI 根据字幕智能选帧</option><option value="scene">按镜头切换选取</option><option value="uniform">按时间平均选取</option>
          </select>
          <select aria-label="画面数量" value={count} disabled={running || frameMode === 'ai'} onChange={event => setCount(Number(event.target.value))}>
            <option value={0}>{frameMode === 'ai' ? 'AI 自主决定（1–16 张）' : <>自动（{automatic} 张）</>}</option>{COUNTS.map(value => <option key={value} value={value}>{value} 张</option>)}
          </select>
          {media ? <button type="button" disabled={previewing || running || frameMode === 'ai'} aria-busy={previewing} onClick={() => { void loadPreview(); }}>
            {previewing ? <><Icon name="loader-circle" className="spinner" />截取中…</> : <><Icon name="image" />{preview ? '重新截取' : '预览画面'}</>}
          </button> : <span className="analysis-hint">归档后可以先预览画面、去掉不要的。</span>}
        </div>
        <p className="analysis-hint">{frameMode === 'ai' ? 'AI 阅读字幕，自行决定画面时间点与张数（最多 16 张），增加一次模型调用；开始分析后执行。' : frameMode === 'scene' ? '检测画面变化优先抽帧；镜头不足或检测失败时按时间均匀补足，不等于理解视频内容。' : '按时长等间隔抽帧，可能错过短暂出现的内容。'}</p>
        {previewError && <p className="error-text" role="alert">{previewError}</p>}
        {preview && <>
          <p className="analysis-hint">点击画面查看大图，勾选框控制是否分析，已选 {kept.size} / {preview.frames.length} 张。</p>
          <ul className="analysis-frames analysis-picker" aria-label="预览画面">
            {preview.frames.map(frame => <li key={frame.name}><label><input type="checkbox" checked={kept.has(frame.name)} disabled={running} onChange={() => toggleFrame(frame.name)} aria-label={`选择画面 ${frame.label}`} />纳入分析</label><button type="button" onClick={() => setLargeFrame(frame)}
              aria-label={`画面 ${frame.label}${kept.has(frame.name) ? '（已选）' : '（未选）'}`}>
              <img src={frame.url} alt="" /><span>{frame.label}</span></button></li>)}
          </ul>
        </>}
      </div>}

      <div className="analysis-block">
        <label className="analysis-label" htmlFor="prompt">{template === 'custom' ? '分析要求' : <>补充要求 <span className="optional">（选填）</span></>}</label>
        <textarea id="prompt" disabled={running} value={prompt} maxLength={4000} rows={2} placeholder={PLACEHOLDERS[template] || '例如：用英文输出 / 重点关注配色'} onChange={event => setPrompt(event.target.value)} />
      </div>

      {estimate && <p className="analysis-estimate">{estimate}。{price}</p>}
      {configured && needsConsent && <label className="consent">
        <input type="checkbox" disabled={running} checked={consent} onChange={event => setConsent(event.target.checked)} />
        <span>{models.native ? '同意由本地 agent 的模型处理课程内容，可能联网并产生费用。' : <>{(usesTranscript || frameMode === 'ai') && usesFrames ? '同意把字幕和画面发送到 ' : usesTranscript ? '同意把字幕发送到 ' : '同意把画面发送到 '}{ai?.provider_name}（{ai ? hostOf(ai.base_url) : ''}），可能产生费用，发送后无法撤销。同意一次后，对这个服务不再询问。</>}</span>
      </label>}
      <div className="analysis-actions">
        <button className="primary operation-button" type="button" aria-busy={running || busy === 'prepare'} disabled={!media || !!blocker || working}
          onClick={() => { if (media) props.onAnalyze(body()); }}>
          {working ? <><Icon name="loader-circle" className="spinner" />{busy === 'prepare' ? '归档中…' : '分析中…'}</>
            : <><Icon name={media ? 'scan-eye' : 'download'} />开始分析</>}
        </button>
        {running && <button type="button" disabled={props.cancelDisabled} onClick={props.onCancel}>取消分析</button>}
        {busy === 'prepare' && <button type="button" onClick={props.onPrepare}>查看下载进度</button>}
        {!working && blocker && parsed && context && <span className="analysis-hint">{blocker}</span>}
      </div>
      {(working || (error && status)) && <p className={error ? 'error-text' : 'analysis-status'} role="status" aria-live="polite">{working ? phase : ''}{status}</p>}
      </div>
    </Panel>

    {(learning || visibleRecords.length > 0) && <Panel className={'analysis-records' + (learning ? ' learning-materials' : '')} aria-labelledby="analysis-records-title">
      <div className="analysis-record-bar">
        <PanelTitle id="analysis-records-title">{learning ? 'AI 学习材料' : '分析记录'}</PanelTitle>
        {learning && <button type="button" className="learning-manage-materials" aria-label="生成/管理 AI 学习材料" title="生成/管理 AI 学习材料" onClick={props.onManage}>管理材料</button>}
        {visibleRecords.length > 0 && <select aria-label={learning ? '选择学习材料' : '选择分析记录'} value={record?.id || ''}
          onChange={event => learning ? chooseLearningMaterial(event.target.value) : setActive(event.target.value)}>
          {visibleRecords.map(item => <option key={item.id} value={item.id}>{item.template_name} · {when(item.created_at)}</option>)}
        </select>}
      </div>
      {learning && loadError && <p className="error-text" role="alert">{learning && loadError}<button type="button" onClick={() => setReload(value => value+1)}>重新载入材料</button></p>}
      {learning && !record && !loadError && <p className="analysis-empty">{!recordsReady ? '正在读取学习材料…' : !media ? '先归档视频，再生成学习材料。' : '还没有当前视频的 AI 学习材料，可按需生成摘要、知识点或章节讲解。'}</p>}
      {record && <article className="analysis-record" aria-label={`${record.template_name}结果`}>
        {!learning && <p className="analysis-meta">{[record.source_label + (record.transcript_label ? `（${record.transcript_label}）` : ''), record.frame_count ? `${record.frame_count} 张画面` : '',
          record.model, record.usage.prompt_tokens ? `用量 ${tokens(record.usage.prompt_tokens)} + ${tokens(record.usage.completion_tokens || 0)} tokens` : '',
          recordCost !== null ? `约 ¥${recordCost > 0 && recordCost < 0.01 ? recordCost.toFixed(4) : recordCost.toFixed(2)}` : ''].filter(Boolean).join(' · ')}</p>}
        {!learning && record.prompt && <p className="analysis-meta">要求：{record.prompt}</p>}
        <div className="analysis-record-actions" hidden={learning}>
          <button type="button" className="primary" disabled={!recordMatches} onClick={useForLearning}
            title={recordMatches ? '在学习视图中使用这份材料' : '这份记录不是当前归档视频的材料'}>用于当前学习</button>
          <button type="button" onClick={() => { void copyRecord(); }}><Icon name="copy" />复制</button>
          <a className="button" href={`/api/workspace/projects/${projectId}/analyses/${record.id}/export?format=md`} download><Icon name="file-down" />导出 Markdown</a>
          <a className="button" href={`/api/workspace/projects/${projectId}/analyses/${record.id}/export?format=docx`} download><Icon name="file-down" />导出 Word</a>
          {!!record.quiz?.questions.length && <a className="button" href={`/api/workspace/projects/${projectId}/analyses/${record.id}/export?format=anki`} download><Icon name="file-down" />导出 Anki</a>}
          {confirmDelete ? <span className="analysis-confirm">删除这条记录和它的画面？
            <button type="button" disabled={working} onClick={() => { void deleteRecord(); }}>删除</button><button type="button" onClick={() => setConfirmDelete(false)}>取消</button></span>
            : <button type="button" className="analysis-delete" disabled={working} onClick={() => setConfirmDelete(true)}><Icon name="trash-2" />删除</button>}
        </div>
        {!learning && !recordMatches && <p className="notice">这份记录未绑定当前归档视频，不能用于当前学习。</p>}
        {!learning && notice && <p className="analysis-hint" role="status">{notice}</p>}
        {!learning && record.frames.length > 0 && <details className="analysis-evidence" open>
          <summary>画面证据（{record.frames.length}）</summary>
          <ul className="analysis-frames" aria-label="这次分析用到的画面">
          {record.frames.map(frame => <li key={frame.url}><button type="button" title={`查看画面 ${frame.label} 大图`} onClick={() => setLargeFrame(frame)}>
            <img src={frame.url} alt={`画面 ${frame.label}`} loading="lazy" /><span>{frame.label}</span></button></li>)}
        </ul></details>}
        {record.quiz && record.quiz.questions.length > 0
          ? <QuizView key={`quiz-${record.id}`} quiz={record.quiz} onSeek={recordSeek}
            projectId={projectId} resource={resource} recordId={record.id} />
          : <Markdown text={record.text} onSeek={recordSeek} maxSeconds={duration} className="analysis-markdown" timeRangesFirst />}
        {record.template === 'quiz' && !record.quiz?.questions.length && <p className="notice" role="status">模型没有返回可解析的题目，下面是原始输出；可以重新生成一次。</p>}

      </article>}
    </Panel>}
  </>;
}
