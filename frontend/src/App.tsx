import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { request } from './lib/api';
import { projectDisplayTitle, videoDisplayTitle } from './lib/projectTitles';
import { useLanguage } from './lib/i18n';
import { cancelTask, isActive, pollJob, remember, remembered, runWorkflow } from './lib/jobs';
import type { TaskInfo, WorkflowJob } from './lib/jobs';
import TaskCenter from './features/tasks/TaskCenter';
import Icon from './components/Icon';
import BiliLogin from './features/subtitles/BiliLogin';
import Transcription from './features/transcription/Transcription';
import Accounts from './features/accounts/Accounts';
import ProjectPanel from './features/library/ProjectPanel';
import ProjectOverview from './features/library/ProjectOverview';
import LearningProgress, { LearningToggle } from './features/library/LearningProgress';
import PlaybackResume from './features/learning/PlaybackResume';
import StudySidebar from './features/learning/StudySidebar';
import StudySplitter from './features/learning/StudySplitter';
import TaskSemanticSearch from './features/tasks/TaskSemanticSearch';
import LearningProjectPicker from './features/learning/LearningProjectPicker';
import SubtitleNaming from './features/naming/SubtitleNaming';
import type { NamingJob } from './features/naming/types';
import type { LearningChat } from './features/learning/AIChat';
import MediaStage from './components/MediaStage';
import SettingsPage from './features/settings/SettingsPage';
import AnalysisPanel from './features/analysis/AnalysisPanel';
import ChapterTimeline from './features/analysis/ChapterTimeline';
import type { AnalysisTimeline } from './features/analysis/chapters';
import { preferredQualityId } from './features/settings/settings';
import { readAutoNext } from './lib/playback';
import usePreferences from './features/settings/usePreferences';
import WorkbenchLayout from './components/WorkbenchLayout';
import WorkbenchPage from './components/WorkbenchPage';
import VideoSourceForm from './components/VideoSourceForm';
import WorkspaceStart from './features/library/WorkspaceStart';
import Panel, { PanelTitle } from './components/Panel';
import FormField from './components/FormField';
import { pageFromSearch, type Page } from './features/navigation/navigation';
import type { AgentTarget, AISettings, AnalysisBody, AnalysisResult, Configuration, LearningEpisode, Media, ParsedVideo, VideoProject, VideoProjectSummary } from './lib/api';

// Re-tested 2026-10-14 (docs/mcp-platform-probe-20261014.md). 小红书 needs a login, so it has no example.
const examples: Record<string, string> = {
  "哔哩哔哩": "https://www.bilibili.com/video/BV1kKWA6uEyr",
  "抖音": "https://www.douyin.com/video/7696088077957090575",
  "YouTube": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
};
const tabs = [
  { name: '视频', id: 0, icon: 'circle-play' },
  { name: '字幕与转写', id: 3, icon: 'captions' },
  { name: 'AI 分析', id: 2, icon: 'scan-eye' },
  { name: '文件重命名', id: 4, icon: 'list-checks' },
] as const;
type Tab = typeof tabs[number]['name'];
type Operation = 'prepare' | 'analyze';
// analysisVersion changes when an analysis or follow-up finishes, so the panel reloads its records.
type ProjectState = { parsed: ParsedVideo; quality: string; status: string; error: boolean; busy: Operation | null; analysisVersion: number; lastRecord?: string; task?: TaskInfo; learningPendingPart: number | null; learningError: string };
type AnalysisRequest = { kind: 'analyze'; body: AnalysisBody } | { kind: 'ask'; recordId: string; question: string; position: number; agent?: AgentTarget; images?: { name: string }[] };
/** A Bilibili season (合集): separate BV ids, unlike the pages (分P) behind "解析视频集数". */
type CollectionItem = { bvid: string; title: string; duration: number | null; current: boolean };
type VideoCollection = { title: string; total: number; items: CollectionItem[] };
// Routine states already shown by the stage, button or quality select; the inspector stays quiet for these.
const QUIET_STATUS = new Set(['等待解析', '解析完成，请选择画质后归档', '已复用归档视频', '已选择归档视频', '所选画质尚未归档', '所选视频已归档', '分析完成', '追问已回答']);
const validId = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
const loginPlatforms: [RegExp, string][] = [[/小红书|xiaohongshu|xhslink/, 'xiaohongshu'], [/抖音|douyin/, 'douyin'], [/B站|bilibili|b23\.tv/, 'bilibili']];
function parseLoginPlatform(message: string, source: string) {
  return loginPlatforms.find(([pattern]) => pattern.test(message) || pattern.test(source))?.[1] || null;
}
function formatDuration(seconds: number) {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

export default function App() {
  useLanguage(); // Re-render on language switch so t() text and translate="no" zones refresh.
  const [page, setPage] = useState<Page>(() => pageFromSearch(location.search));
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobile, setMobile] = useState(() => matchMedia('(max-width:760px)').matches);
  const [source, setSource] = useState('');
  const [projectStates, setProjectStates] = useState<Record<string, ProjectState>>({});
  const projects = useRef(projectStates);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const selected = selectedId ? projectStates[selectedId] : undefined;
  const parsed = selected?.parsed || null;
  const quality = selected?.quality || '';
  const media = parsed?.qualities.find(item => item.id === quality)?.media || null;
  const busy = selected?.busy || null;
  const [parsing, setParsing] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [learningNavigationError, setLearningNavigationError] = useState('');
  const restoringRequest = useRef<AbortController | null>(null);
  const [parseStatus, setParseStatus] = useState('等待解析');
  const [parseError, setParseError] = useState(false);
  useEffect(() => {
    if (!parsing) return;
    const timer = window.setTimeout(() => setParseStatus('仍在读取视频信息。部分平台响应较慢，请保留当前页面，不要重复提交。'), 3000);
    return () => window.clearTimeout(timer);
  }, [parsing]);
  const [tab, setTab] = useState<Tab>(() => new URLSearchParams(location.search).get('page') === 'transcription' ? '字幕与转写' : '视频');
  // Both entry points share mounted content; the workbench keeps its own selected tab.
  const activeTab: Tab = page === 'learning' ? 'AI 分析' : tab;
  const learning = page === 'learning';
  const managing = !learning && activeTab === 'AI 分析';
  // 生成字幕 → 字幕与转写 with the current video selected; B站 login from the quality notice.
  const [subtitleFocus, setSubtitleFocus] = useState(0);
  const [qualityLogin, setQualityLogin] = useState<'closed' | 'open' | 'done'>('closed');
  const [accountFocus, setAccountFocus] = useState<string | null>(null);
  function openAccounts(platform: string | null = null) { setAccountFocus(platform); navigate('accounts', true); }
  const [config, setConfig] = useState<Configuration | null>(null);
  const [configError, setConfigError] = useState('');
  const { settings, saveStatus: settingsSaveStatus, saveSettings } = usePreferences();
  const settingsRef = useRef(settings); settingsRef.current = settings;
  const [settingsVisited, setSettingsVisited] = useState(page === 'settings');
  const [settingsFocusRequest, setSettingsFocusRequest] = useState(0);
  useEffect(() => { if (page === 'settings') setSettingsVisited(true); }, [page]);
  const [archiveReminder, setArchiveReminder] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [analysisTimeline, setAnalysisTimeline] = useState<AnalysisTimeline | null>(null);
  const [learningChat, setLearningChat] = useState<LearningChat | null>(null);
  const pendingSeek = useRef<{ video: HTMLVideoElement; go: () => void } | null>(null);
  useEffect(() => () => {
    if (pendingSeek.current) { pendingSeek.current.video.removeEventListener('loadedmetadata', pendingSeek.current.go); pendingSeek.current = null; }
  }, [parsed?.project.id, media?.resource, media?.preview_url]);
  const [coverError, setCoverError] = useState(false);
  const [videoError, setVideoError] = useState(false);
  const [studyCollapsed, setStudyCollapsed] = useState(false);
  const [namingVisited, setNamingVisited] = useState(false);
  useEffect(() => { if (page === 'workspace' && tab === '文件重命名') setNamingVisited(true); }, [page, tab]);
  const [namingTask, setNamingTask] = useState<{ projectId: string; id: string } | null>(null);
  const [libraryVersion, setLibraryVersion] = useState(0);
  const resumeBar = useRef<HTMLElement>(null);
  const [activeCount, setActiveCount] = useState<number | null>(null);
  const [cancellingTask, setCancellingTask] = useState<string | null>(null);
  const parseEpoch = useRef(0);
  const parsingRequest = useRef<AbortController | null>(null);
  const workflowRequests = useRef(new Map<string, AbortController>());
  const learningRequests = useRef(new Map<string, AbortController>());
  const restoreRequests = useRef(new Set<AbortController>());
  const projectNavigation = useRef(0);
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const sourceInput = useRef<HTMLTextAreaElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const pageRef = useRef(page); pageRef.current = page;

  function updateProject(id: string, update: (value: ProjectState) => ProjectState) {
    const value = projects.current[id];
    if (!value) return;
    const next = { ...projects.current, [id]: update(value) };
    projects.current = next; setProjectStates(next);
  }
  function installProject(result: ParsedVideo, restoring = false) {
    const id = result.project.id;
    const previous = projects.current[id];
    // An in-flight job owns its parsed session/quality until its result is applied.
    if (restoring && previous?.busy) return id;
    const chosen = previous?.busy ? previous.quality : restoring ? result.default_quality_id || '' : preferredQualityId(result.qualities, result.default_quality_id, settingsRef.current.qualityPreference);
    const next: ProjectState = {
      parsed: result, quality: chosen, status: previous?.busy ? previous.status : result.warning || (result.qualities.find(item => item.id === chosen)?.media ? '已复用归档视频' : '解析完成，请选择画质后归档'),
      error: false, busy: previous?.busy || null, analysisVersion: previous?.analysisVersion || 0, lastRecord: previous?.lastRecord,
      learningPendingPart: previous?.learningPendingPart ?? null, learningError: previous?.learningError || '',
    };
    projects.current = { ...projects.current, [id]: next }; setProjectStates(projects.current);
    return id;
  }
  // A course rename also applies to its other archived episodes that are open in this page.
  function renameProject(project: VideoProject, renamed: string[]) {
    setLibraryVersion(value => value + 1);
    for (const id of new Set([project.id, ...renamed])) updateProject(id, value => ({ ...value,
      parsed: { ...value.parsed, title: project.title, project: { ...value.parsed.project, title: project.title, display_title: project.display_title } } }));
  }
  function selectProject(id: string) {
    const value = projects.current[id]; if (!value) return;
    selectedRef.current = id; setSelectedId(id); setSource(value.parsed.source_url);
    setCoverError(false); setVideoError(false); setParseError(false); setArchiveReminder(false);
    const url = new URL(location.href); url.searchParams.set('project', id); history.replaceState(null, '', url);
    try { localStorage.setItem('video-parser:last-project', id); localStorage.setItem('video-parser:last-quality', value.parsed.qualities.find(item => item.id === value.quality)?.media?.resource || ''); } catch { /* Optional restore. */ }
  }
  function navigate(next: Page, focus = false) {
    if (next === 'transcription') { setTab('字幕与转写'); next = 'workspace'; }
    const changed = next !== pageRef.current;
    if (changed) {
      const url = new URL(location.href);
      if (next === 'workspace') url.searchParams.delete('page');
      else url.searchParams.set('page', next);
      history.pushState(null, '', url);
    }
    pageRef.current = next; setPage(next); setMenuOpen(false);
    if (changed || focus) requestAnimationFrame(() => {
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
      if (focus) document.getElementById('page-title-' + next)?.focus({ preventScroll: true });
    });
  }
  const [settingsFocusTarget, setSettingsFocusTarget] = useState<'api' | 'sources'>('api');
  function openSettings(button: HTMLButtonElement, ai = false) {
    setSettingsFocusTarget(button.dataset.settingsTarget === 'sources' ? 'sources' : 'api');
    if (ai) setSettingsFocusRequest(value => value + 1);
    navigate('settings', true);
  }
  function updateAI(ai: AISettings) { setConfig(current => current && { ...current, ai, ai_configured: ai.configured }); }
  function updateArchiveRoot(archiveRoot: string) {
    setConfig(current => current && { ...current, archive_root: archiveRoot, archive_root_source: 'settings' });
    setConfigError('');
  }
  async function restoreProject(id: string, qualityResource = '', focus = true, preserveTab = false) {
    if (!validId(id)) return;
    restoringRequest.current?.abort();
    if (parsingRequest.current) { ++parseEpoch.current; parsingRequest.current.abort(); parsingRequest.current = null; setParsing(false); }
    setRestoringId(id); setLearningNavigationError('');
    const version = ++projectNavigation.current;
    const parseVersion = parseEpoch.current;
    const controller = new AbortController(); restoreRequests.current.add(controller);
    restoringRequest.current = controller;
    try {
      const result = await request<ParsedVideo & { analysis: string }>(`/projects/${id}/resume?quality_id=${encodeURIComponent(qualityResource)}`, controller.signal);
      if (controller.signal.aborted) return;
      const restoredId = installProject(result, true);
      if (version !== projectNavigation.current || parseVersion !== parseEpoch.current) return;
      selectProject(restoredId); if (!preserveTab) setTab('视频'); if (focus) navigate('workspace', true);
      return restoredId;
    } catch (reason) {
      if (!controller.signal.aborted && version === projectNavigation.current && parseVersion === parseEpoch.current) {
        const message = reason instanceof Error ? reason.message : '无法恢复视频项目。';
        if (focus) { setParseStatus(message); setParseError(true); navigate('workspace', true); }
        else if (pageRef.current === 'learning' || pageRef.current === 'workspace') setLearningNavigationError(message);
      }
    } finally {
      restoreRequests.current.delete(controller);
      if (restoringRequest.current === controller) { restoringRequest.current = null; setRestoringId(null); }
    }
  }

  async function restoreNamingTask(identifier: string) {
    const controller = new AbortController(); restoreRequests.current.add(controller);
    let version = ++projectNavigation.current;
    const parseVersion = parseEpoch.current; setLearningNavigationError('');
    try {
      const job = await request<NamingJob>('/naming/tasks/' + identifier, controller.signal);
      if (controller.signal.aborted || version !== projectNavigation.current || parseVersion !== parseEpoch.current) return;
      if (!validId(job.project_id)) throw new Error('命名任务缺少有效的项目关联。');
      videoRef.current?.pause();
      ++version;
      const restored = await restoreProject(job.project_id, '', false);
      if (controller.signal.aborted || version !== projectNavigation.current) return;
      if (restored !== job.project_id) throw new Error('无法从本地归档恢复命名任务对应的视频；请检查项目与归档后重试。');
      setNamingTask({ projectId: job.project_id, id: identifier }); setNamingVisited(true); setTab('文件重命名'); navigate('workspace', true);
    } catch (reason) {
      if (!controller.signal.aborted && version === projectNavigation.current) setLearningNavigationError(reason instanceof Error ? reason.message : '无法读取命名任务。');
    } finally { restoreRequests.current.delete(controller); }
  }

  // Workflow controllers belong to projects, not to the currently displayed page/link.
  function watchWorkflow(job: WorkflowJob<Media | AnalysisResult>, controller: AbortController) {
    const id = job.project_id;
    if (!id || !projects.current[id]) return;
    const operation: Operation = job.kind === 'analysis' ? 'analyze' : 'prepare';
    workflowRequests.current.set(id, controller);
    updateProject(id, value => ({ ...value, busy: operation, status: job.message, task: job, error: false }));
    void (async () => {
      try {
        const final = isActive(job.state) ? await pollJob<WorkflowJob<Media | AnalysisResult>>('/tasks/' + job.id, controller.signal,
          item => updateProject(id, value => ({ ...value, status: item.message, task: item }))) : job;
        if (controller.signal.aborted) return;
        if (final.state !== 'completed') throw new Error(final.message);
        applyWorkflowResult(id, final.result);
      } catch (reason) {
        if (!controller.signal.aborted) updateProject(id, value => ({ ...value, error: true, status: reason instanceof Error ? reason.message : '任务状态读取失败，请在任务中心重试查看。' }));
      } finally {
        if (workflowRequests.current.get(id) === controller) { workflowRequests.current.delete(id); updateProject(id, value => ({ ...value, busy: null })); }
      }
    })();
  }
  function applyWorkflowResult(id: string, result: Media | AnalysisResult) {
    if ('text' in result) updateProject(id, value => ({ ...value, analysisVersion: value.analysisVersion + 1, lastRecord: result.record?.id,
      parsed: result.project ? { ...value.parsed, project: result.project } : value.parsed, status: result.mode === 'ask' || result.mode === 'conversation' ? '追问已回答' : '分析完成', error: false }));
    else {
      // A newly archived video changes /projects, so the shared picker cache must drop it.
      if (!projects.current[id]?.parsed.qualities.find(item => item.id === result.quality_id)?.media) setLibraryVersion(value => value + 1);
      updateProject(id, value => ({ ...value, parsed: { ...value.parsed,
        qualities: value.parsed.qualities.map(item => item.id === result.quality_id ? { ...item, media: result } : item),
        episodes: value.parsed.episodes?.map(episode => episode.part === value.parsed.project.part
          ? { ...episode, project_id: value.parsed.project.id, archived: true } : episode) }, status: '所选视频已归档', error: false }));
      // The unified media stage switches from progress to the archived player in place.
    }
  }
  async function restoreWorkflow(identifier: string, explicit: boolean) {
    const controller = new AbortController(); restoreRequests.current.add(controller);
    const version = projectNavigation.current;
    const parseVersion = parseEpoch.current;
    try {
      const job = await request<WorkflowJob<Media | AnalysisResult>>('/tasks/' + identifier, controller.signal);
      if (!validId(job.project_id) || !['video', 'analysis'].includes(job.kind || '')) return;
      const id = job.project_id;
      if (!projects.current[id]) {
        const resumed = await request<ParsedVideo & { analysis: string }>(`/projects/${id}/resume`, controller.signal);
        if (controller.signal.aborted) return;
        installProject(resumed, true);
      }
      if (explicit && version === projectNavigation.current && parseVersion === parseEpoch.current) {
        // Resumed projects key archived files as saved-*, so match the finished task by its archived resource first.
        if (job.state === 'completed' && job.result && 'quality_id' in job.result) {
          const result = job.result as Media;
          updateProject(id, value => {
            const match = value.parsed.qualities.find(item => item.media?.resource === result.resource) || value.parsed.qualities.find(item => item.id === result.quality_id);
            return match ? { ...value, quality: match.id } : value;
          });
        }
        selectProject(id); setTab(job.kind === 'analysis' ? 'AI 分析' : '视频'); navigate('workspace', true);
      }
      if (isActive(job.state)) {
        if (workflowRequests.current.has(id)) return;
        watchWorkflow(job, controller);
      }
      else if (job.state === 'completed' && explicit) applyWorkflowResult(id, job.result);
      else if (explicit) updateProject(id, value => ({ ...value, error: true, status: job.message, task: job }));
    } catch (reason) {
      if (!controller.signal.aborted && explicit) { setParseStatus(reason instanceof Error ? reason.message : '无法读取任务结果。'); setParseError(true); }
    } finally { restoreRequests.current.delete(controller); }
  }
  useEffect(() => {
    const controller = new AbortController();
    request<Configuration>('/config', controller.signal).then(setConfig).catch((reason: unknown) => {
      if (!controller.signal.aborted) setConfigError(reason instanceof Error ? reason.message : '无法读取服务配置。');
    });
    try {
      const last = new URLSearchParams(location.search).get('project') || localStorage.getItem('video-parser:last-project');
      // Background restore must not replace a deep-linked or newly selected workbench tab.
      if (last) void restoreProject(last, localStorage.getItem('video-parser:last-quality') || '', false, true);
    } catch { /* Restore cannot block a fresh start. */ }
    for (const kind of ['video', 'analysis']) { const id = remembered(kind); if (id) void restoreWorkflow(id, false); }
    const restore = (event: Event) => {
      const detail = (event as CustomEvent<{ kind?: string; id?: string; projectId?: string }>).detail;
      if (detail?.kind === 'naming' && validId(detail.id)) { void restoreNamingTask(detail.id); return; }
      if (validId(detail?.projectId)) { void restoreProject(detail.projectId); return; }
      if (!validId(detail?.id)) return;
      if (detail.kind === 'transcriptions') navigate('transcription', true);
      else if (detail.kind === 'subtitles') navigate('transcription', true);
      else if (['video', 'analysis'].includes(detail.kind || '')) { ++projectNavigation.current; void restoreWorkflow(detail.id, true); }
    };
    window.addEventListener('video-parser:restore-task', restore);
    return () => {
      controller.abort(); parsingRequest.current?.abort();
      for (const request of workflowRequests.current.values()) request.abort();
      for (const request of learningRequests.current.values()) request.abort();
      for (const request of restoreRequests.current) request.abort();
      window.removeEventListener('video-parser:restore-task', restore);
    };
  }, []);
  useEffect(() => {
    const restorePage = () => {
      const next = pageFromSearch(location.search);
      pageRef.current = next; setPage(next); setMenuOpen(false);
      requestAnimationFrame(() => {
        window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
        document.getElementById('page-title-' + next)?.focus({ preventScroll: true });
      });
    };
    window.addEventListener('popstate', restorePage);
    return () => window.removeEventListener('popstate', restorePage);
  }, []);
  useEffect(() => {
    const query = matchMedia('(max-width:760px)');
    const change = () => { setMobile(query.matches); if (!query.matches) setMenuOpen(false); };
    query.addEventListener('change', change); return () => query.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    sidebar.current?.querySelector<HTMLButtonElement>('[aria-current="page"]')?.focus();
    const key = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setMenuOpen(false); requestAnimationFrame(() => menuButton.current?.focus()); }
      if (event.key !== 'Tab') return;
      const buttons = Array.from(sidebar.current?.querySelectorAll<HTMLElement>('button, a[href]') || []);
      if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0]?.focus(); }
    };
    document.addEventListener('keydown', key); return () => document.removeEventListener('keydown', key);
  }, [menuOpen]);

  // Grow the link box with its content (CSS max-height caps it) so long share text is never clipped.
  useLayoutEffect(() => {
    const element = sourceInput.current; if (!element) return;
    element.style.height = 'auto';
    element.style.height = element.scrollHeight + (element.offsetHeight - element.clientHeight) + 'px';
  }, [source, mobile, page, activeTab]);
  function clearSource(next = '') {
    restoringRequest.current?.abort();
    ++parseEpoch.current; ++projectNavigation.current; parsingRequest.current?.abort(); parsingRequest.current = null;
    setParsing(false); setSource(next); setSelectedId(null); selectedRef.current = null;
    setParseError(false); setParseStatus('等待解析'); setArchiveReminder(false); setCoverError(false); setVideoError(false);
    const url = new URL(location.href); url.searchParams.delete('project'); history.replaceState(null, '', url);
    try { localStorage.removeItem('video-parser:last-project'); localStorage.removeItem('video-parser:last-quality'); } catch { /* Optional restore. */ }
  }
  async function parse(text = source, targetTab: Tab = '视频') {
    if (!text.trim() || parsingRequest.current) return;
    restoringRequest.current?.abort();
    ++projectNavigation.current; const epoch = ++parseEpoch.current;
    const controller = new AbortController(); parsingRequest.current = controller;
    setParsing(true); setParseError(false); setParseStatus('正在解析链接…');
    try {
      const result = await request<ParsedVideo>('/parse', controller.signal, { text });
      if (controller.signal.aborted || epoch !== parseEpoch.current) return;
      const id = installProject(result); selectProject(id); setTab(targetTab); setParseStatus('');
    } catch (reason) {
      if (!controller.signal.aborted && epoch === parseEpoch.current) { setParseError(true); setParseStatus(reason instanceof Error ? reason.message : '解析失败，请检查链接后重试。'); }
    } finally { if (epoch === parseEpoch.current) { parsingRequest.current = null; setParsing(false); } }
  }
  async function openLearningProject(id: string, preserveTab = false) {
    if (parsingRequest.current || restoringRequest.current) return;
    // Flush the old video's pause/progress before changing the media context.
    videoRef.current?.pause();
    await restoreProject(id, '', false, preserveTab);
  }
  async function selectSubtitleSource(id: string, resource: string) {
    if (parsingRequest.current) return false;
    const current = selectedRef.current ? projects.current[selectedRef.current] : undefined;
    if (!restoringRequest.current && selectedRef.current === id && current?.parsed.qualities.find(item => item.id === current.quality)?.media?.resource === resource) return true;
    videoRef.current?.pause();
    return await restoreProject(id, resource, false, true) === id;
  }
  const [refreshingEpisodes, setRefreshingEpisodes] = useState(false);
  const [episodeMessage, setEpisodeMessage] = useState('');
  const [collection, setCollection] = useState<VideoCollection | null>(null);
  async function refreshEpisodes() {
    if (!parsed || refreshingEpisodes) return;
    const id = parsed.project.id; setRefreshingEpisodes(true); setEpisodeMessage('');
    try { const result = await request<{ episodes: LearningEpisode[]; collection?: VideoCollection | null }>(`/projects/${id}/episodes/refresh`, new AbortController().signal, { confirm: true });
      updateProject(id, current => ({ ...current, parsed: { ...current.parsed, episodes: result.episodes } }));
      setCollection(result.collection ?? null);
      setEpisodeMessage(result.episodes.length ? `已解析 ${result.episodes.length} 集。`
        : result.collection ? `该视频属于合集《${result.collection.title}》，共 ${result.collection.total} 个视频；点击任意一项即可解析导入。`
        : '当前链接为单集视频。');
    } catch (e) { setEpisodeMessage(e instanceof Error ? e.message : '解析分集失败'); } finally { setRefreshingEpisodes(false); }
  }
  function importCollectionItem(bvid: string) {
    if (busy !== null || parsing) return;
    const url = `https://www.bilibili.com/video/${bvid}`;
    setSource(url);
    void parse(url);
  }
  // A season belongs to the project that was refreshed; switching projects must not keep it.
  useEffect(() => { setCollection(null); setEpisodeMessage(''); }, [parsed?.project.id]);
  async function selectEpisode(part: number) {
    const id = selectedRef.current, value = id ? projects.current[id] : undefined;
    if (!id || !value || parsingRequest.current || restoringRequest.current) return;
    const episode = value.parsed.episodes?.find(item => item.part === part);
    if (episode?.archived && validId(episode.project_id)) { await openLearningProject(episode.project_id); return; }
    videoRef.current?.pause();
    try {
      const url = new URL(value.parsed.source_url);
      url.searchParams.set('p', String(part));
      await parse(url.toString(), tab);
    } catch {
      updateProject(id, current => ({ ...current, learningError: '无法打开这一分集，请重新解析视频后重试。' }));
    }
  }
  async function setEpisodeLearned(part: number, learned: boolean) {
    const id = selectedRef.current, value = id ? projects.current[id] : undefined;
    if (!id || !value || value.busy !== null || parsingRequest.current || learningRequests.current.has(id)
      || (part !== (value.parsed.project.part || 1) && !value.parsed.episodes?.some(episode => episode.part === part))) return;
    const controller = new AbortController();
    learningRequests.current.set(id, controller);
    updateProject(id, current => ({ ...current, learningPendingPart: part, learningError: '' }));
    try {
      const result = await request<{ episodes: LearningEpisode[]; learned?: boolean }>(`/projects/${id}/learning`, controller.signal, { part, learned });
      if (controller.signal.aborted) return;
      updateProject(id, current => {
        const episodes = Array.isArray(result.episodes) ? result.episodes : current.parsed.episodes || [];
        const currentLearned = typeof result.learned === 'boolean' ? result.learned
          : episodes.find(episode => episode.part === current.parsed.project.part)?.learned;
        return { ...current, parsed: { ...current.parsed, episodes,
          project: { ...current.parsed.project, learned: typeof currentLearned === 'boolean' ? currentLearned : current.parsed.project.learned } }, learningError: '' };
      });
    } catch (reason) {
      if (!controller.signal.aborted) updateProject(id, current => ({ ...current, learningError: reason instanceof Error ? reason.message : '学习进度保存失败，请重试。' }));
    } finally {
      if (learningRequests.current.get(id) === controller) {
        learningRequests.current.delete(id);
        if (!controller.signal.aborted) updateProject(id, current => ({ ...current, learningPendingPart: null }));
      }
    }
  }
  // A finished episode continues with the next one while the learner keeps auto-advance on.
  const selectEpisodeRef = useRef(selectEpisode);
  useEffect(() => { selectEpisodeRef.current = selectEpisode; });
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !learning) return;
    const advance = () => {
      if (!readAutoNext()) return;
      const id = selectedRef.current, value = id ? projects.current[id] : undefined;
      const episodes = value?.parsed.episodes;
      if (!episodes || episodes.length < 2) return;
      const current = value.parsed.project.part || 1;
      const next = episodes.map(item => item.part).filter(part => part > current).sort((first, second) => first - second)[0];
      if (next !== undefined) void selectEpisodeRef.current(next);
    };
    video.addEventListener('ended', advance);
    return () => video.removeEventListener('ended', advance);
  }, [learning, media?.resource, parsed?.project.id, parsed?.project.part, parsed?.episodes]);
  function chooseQuality(key: string) {
    if (!selectedId || busy) return;
    updateProject(selectedId, value => ({ ...value, quality: key, error: false, status: value.parsed.qualities.find(item => item.id === key)?.media ? '已选择归档视频' : '所选画质尚未归档' }));
    setVideoError(false);
  }
  // Reads the latest project state (not this render's), so 归档并分析 can chain prepare → analyze.
  async function run(operation: Operation, analysis?: AnalysisRequest): Promise<boolean> {
    const id = selectedRef.current, value = id ? projects.current[id] : undefined;
    if (!id || !value || workflowRequests.current.has(id) || !value.quality) return false;
    const selectedQuality = value.quality, parsedId = value.parsed.id;
    const archived = value.parsed.qualities.find(item => item.id === selectedQuality)?.media || null;
    if (operation === 'prepare' && archived) return true;
    if (operation === 'analyze' && (!archived || !analysis)) return false;
    const controller = new AbortController(); workflowRequests.current.set(id, controller);
    // Keep the current content tab; its shared media stage owns cover/download/player states.
    const path = operation === 'prepare' ? `/videos/${parsedId}/prepare-task`
      : analysis!.kind === 'ask' ? `/projects/${value.parsed.project.id}/conversations/${analysis!.recordId}/ask-task` : `/videos/${parsedId}/analyze-task`;
    const body = operation === 'prepare' ? { quality_id: selectedQuality }
      : analysis!.kind === 'ask' ? { consent: true, question: analysis!.question, position: analysis!.position, resource: archived!.resource, agent: analysis!.agent, images: analysis!.images ?? [] } : { ...analysis!.body, consent: true, quality_id: selectedQuality };
    updateProject(id, current => ({ ...current, busy: operation, task: undefined, error: false,
      status: operation === 'prepare' ? '正在下载所选画质并归档…' : analysis!.kind === 'ask' ? '正在发送追问…' : '正在准备分析材料…' }));
    try {
      const result = await runWorkflow<Media | AnalysisResult>(path, body, controller.signal,
        job => { if (!controller.signal.aborted) { remember(operation === 'prepare' ? 'video' : 'analysis', job.id); updateProject(id, current => ({ ...current, status: job.message, task: job })); } });
      if (controller.signal.aborted) return false;
      applyWorkflowResult(id, result);
      if (selectedRef.current === id) {
        setVideoError(false);
        if (operation === 'prepare') {
          if (settingsRef.current.completionReminder) setArchiveReminder(true);
          try { if ('resource' in result) localStorage.setItem('video-parser:last-quality', result.resource); } catch { /* Optional restore. */ }
        }
      }
      return true;
    } catch (reason) {
      if (!controller.signal.aborted) updateProject(id, current => ({ ...current, error: true, status: reason instanceof Error ? reason.message : '操作失败，请重试。' }));
      return false;
    } finally {
      if (workflowRequests.current.get(id) === controller) { workflowRequests.current.delete(id); updateProject(id, current => ({ ...current, busy: null })); }
    }
  }
  function seekVideo(seconds: number) {
    if (!Number.isFinite(seconds) || seconds < 0) return;
    const video = videoRef.current; if (!video) return;
    const sourceURL = video.getAttribute('src');
    requestAnimationFrame(() => {
      if (video !== videoRef.current || video.getAttribute('src') !== sourceURL) return;
      if (pendingSeek.current) pendingSeek.current.video.removeEventListener('loadedmetadata', pendingSeek.current.go);
      // Seeking a paused video must not start playback; only resume what was already playing.
      const wasPlaying = !video.paused;
      const go = () => {
        if (video !== videoRef.current || video.getAttribute('src') !== sourceURL) return;
        pendingSeek.current = null;
        video.currentTime = Number.isFinite(video.duration) ? Math.min(seconds, Math.max(0, video.duration - .01)) : seconds;
        if (wasPlaying) void video.play().catch(() => undefined);
      };
      if (video.readyState >= 1) go(); else { pendingSeek.current = { video, go }; video.addEventListener('loadedmetadata', go, { once: true }); video.load(); }
      document.getElementById('video-stage')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  }
  function generateSubtitles() { setSubtitleFocus(value => value + 1); setTab('字幕与转写'); navigate('workspace', true); }
  async function cancelWorkflow() {
    const id = selectedId, task = selected?.task;
    if (!id || !task || !isActive(task.state) || cancellingTask) return;
    setCancellingTask(task.id);
    try { await cancelTask(task.kind || (selected?.busy === 'analyze' ? 'analysis' : 'video'), task.id); }
    catch (reason) { updateProject(id, value => ({ ...value, error: true, status: reason instanceof Error ? reason.message : '取消请求失败，请重试。' })); }
    finally { setCancellingTask(null); }
  }
  // Parse progress/errors are shown once under the link box, not again in the inspector.
  const inspectorStatus = parsing || parseError ? '' : selected?.status || '';
  const showStatus = !!inspectorStatus && (!!selected?.error || busy !== null || !QUIET_STATUS.has(inspectorStatus));
  const cancelDisabled = !selected?.task || !isActive(selected.task.state) || selected.task.state === 'cancelling' || cancellingTask !== null;
  const fullInspector = !learning && activeTab === '视频';
  const archiveProblem = !!selected?.error && !media && busy !== 'analyze';
  const currentTimeline = analysisTimeline?.projectId === parsed?.project.id ? analysisTimeline : null;
  const currentChat = learningChat?.projectId === parsed?.project.id && learningChat?.resource === media?.resource ? learningChat : null;
  const timelineEnabled = !!media && !!currentTimeline && (currentTimeline.videoResource
    ? currentTimeline.videoResource === media.resource : (parsed?.episodes?.length || 0) <= 1);
  function openPreparation() { setTab('视频'); navigate('workspace', true); }
  function openAnalysis() { setTab('AI 分析'); navigate('workspace', true); }
  function openLearning() { navigate('learning', true); }
  async function openOverviewProject(project: VideoProjectSummary, target: 'workspace' | 'subtitles' | 'learning') {
    videoRef.current?.pause();
    const restored = await restoreProject(project.id, '', false);
    if (!restored) throw new Error('无法打开这个视频，请刷新视频总览后重试。');
    if (target === 'subtitles') { setTab('字幕与转写'); navigate('workspace', true); return; }
    if (target === 'learning') { setTab('AI 分析'); navigate('learning', true); return; }
    setTab('视频'); navigate('workspace', true);
  }
  // Short side, so a 480x852 portrait stream also counts as limited.
  const maxShortSide = Math.max(0, ...(parsed?.qualities.map(item => item.width && item.height ? Math.min(item.width, item.height) : item.height || 0) || []));
  const qualityLimited = maxShortSide > 0 && maxShortSide < 720;
  const parseNeedsLogin = parseError && parseStatus.includes('账号与登录');
  const platformDuration = typeof parsed?.duration === 'number' && parsed.duration > 0 ? parsed.duration : null;
  const projectTitle = parsed ? projectDisplayTitle(parsed.project) : '';
  const videoTitle = parsed ? videoDisplayTitle(parsed.project) : '';
  const catalogEpisode = parsed?.episodes?.find(episode => episode.part === parsed.project.part);
  const currentEpisode: LearningEpisode | undefined = parsed ? catalogEpisode || {
    part: parsed.project.part || 1, title: videoTitle,
    duration: parsed.duration ?? null, project_id: parsed.project.id, archived: !!media, learned: parsed.project.learned === true,
  } : undefined;
  function moveTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
    if (next === null) return; event.preventDefault(); setTab(tabs[next].name); tabButtons.current[next]?.focus();
  }

  const sourceEntry = <VideoSourceForm value={source} parsing={parsing} inputRef={sourceInput} onChange={setSource}
    onParse={() => { void parse(); }} onClear={() => { clearSource(); sourceInput.current?.focus(); }} />;
  const episodeToggle = currentEpisode && <LearningToggle episode={currentEpisode} placement="player"
    disabled={parsing || !!restoringId || busy !== null || (selected?.learningPendingPart !== null && selected?.learningPendingPart !== undefined)}
    onChange={learned => { void setEpisodeLearned(currentEpisode.part, learned); }} />;
  const episodeLearning = parsed && parsed.episodes && parsed.episodes.length > 1 && <LearningProgress key={parsed.project.id}
    project={parsed.project} episodes={parsed.episodes} disabled={parsing || !!restoringId || busy !== null}
    savingPart={selected?.learningPendingPart ?? null} error={selected?.learningError || ''}
    onSelectEpisode={part => { void selectEpisode(part); }}
    onSetLearned={(part, learned) => { void setEpisodeLearned(part, learned); }} />;
  const projectPicker = <LearningProjectPicker context={learning ? 'learning' : 'workspace'} compact={!!parsed || !learning} current={parsed?.project || null} version={libraryVersion} disabled={parsing || !!restoringId}
    loading={!!restoringId} onSelect={id => { void openLearningProject(id, !learning); }} />;
  return <WorkbenchLayout page={page} mobile={mobile} menuOpen={menuOpen} activeCount={activeCount}
    sidebarRef={sidebar} menuButtonRef={menuButton} onNavigate={navigate}
    onOpenMenu={() => setMenuOpen(true)}
    onCloseMenu={() => { setMenuOpen(false); requestAnimationFrame(() => menuButton.current?.focus()); }}
    overlays={<>
      {archiveReminder && <div className="archive-reminder" role="status" aria-live="polite">视频已归档<button type="button" aria-label="关闭归档提醒" onClick={() => setArchiveReminder(false)}>×</button></div>}
    </>}>
        {configError && <p className="error-text configuration-error" role="alert">{configError}</p>}
        {page === 'workspace' && learningNavigationError && <p className="error-text configuration-error" role="alert">{learningNavigationError}</p>}
        <WorkbenchPage page="overview" activePage={page}>
          <ProjectOverview visible={page === 'overview'} version={libraryVersion} currentId={parsed?.project.id || null}
            onOpen={openOverviewProject} />
        </WorkbenchPage>
        <WorkbenchPage page={page === 'learning' ? 'learning' : 'workspace'} activePage={page}>
        {(page === 'workspace' || page === 'learning') && config && !config.media_ready && <p className="error-text media-setup-error" role="alert">未检测到 ffmpeg 或 ffprobe，请安装并加入 PATH 后重启服务。</p>}
          <div className="workspace-toolbar" hidden={learning}>
              <div className="tabs" hidden={page === 'learning'} role="tablist" aria-label="内容类型">{tabs.map((item, index) => <button key={item.id} ref={element => { tabButtons.current[index] = element; }} id={'tab-' + item.id} role="tab" aria-selected={tab === item.name} aria-controls={item.name === '字幕与转写' ? 'subtitle-workpanel' : item.name === '文件重命名' ? 'naming-workpanel' : 'workpanel'} tabIndex={tab === item.name ? 0 : -1} onClick={() => setTab(item.name)} onKeyDown={event => moveTab(event, index)}>
          <Icon name={item.icon} />{item.name}
          </button>)}
          </div>
          {!learning && projectPicker}
          </div>
          <div hidden={!learning && !(parsed && (managing || activeTab === '文件重命名'))} className={learning ? `learning-context-header${!parsed ? ' is-empty' : ''}` : 'workspace-context-header'}>
          {learning && <div className="learning-header-completion">{episodeToggle}</div>}
          {learning && projectPicker}
          {learning && selected?.learningError && (parsed?.episodes?.length || 0) <= 1 && <p className="error-text" role="alert">{selected.learningError}</p>}
          {learning && learningNavigationError && <p className="error-text" role="alert">{learningNavigationError} 当前视频与学习数据保持不变。</p>}
          {parsed && (learning || managing || activeTab === '文件重命名') && <div className="video-context-heading">
            <div><strong translate="no" title={videoTitle}>{videoTitle}</strong>
              {learning ? <span translate="no">{[parsed.project.part ? `P${parsed.project.part}` : '', projectTitle !== videoTitle ? projectTitle : !parsed.project.episode_title ? catalogEpisode?.title : ''].filter(Boolean).join(' · ')}</span>
                : <span translate="no">{[parsed.project.part ? `P${parsed.project.part}` : '', projectTitle !== videoTitle ? projectTitle : ''].filter(Boolean).join(' · ')}</span>}</div>
          </div>}
          </div>
          {!learning && !parsed && tab !== '字幕与转写' && tab !== '文件重命名' && <WorkspaceStart onContinue={openLearning}>
            <Panel className="source-panel" aria-labelledby="source-label">{sourceEntry}</Panel>
            {!source && <div className="examples-row"><div className="examples"><span>试试示例</span>
              {Object.entries(examples).map(([name, url]) => <button key={name} className="chip" type="button" onClick={() => { setSource(url); sourceInput.current?.focus(); }}>{name}</button>)}
            </div></div>}
          </WorkspaceStart>}
          {fullInspector && parsed && <Panel className="source-panel" aria-labelledby="source-label">{sourceEntry}</Panel>}
          {(parsing || parseError) && <p className={parseError ? 'error-text' : 'notice'} role="status">{parseStatus}
          {parseNeedsLogin && <button type="button" className="link-button" onClick={() => openAccounts(parseLoginPlatform(parseStatus, source))}>去登录</button>}
          </p>}
          <div key="workpanel" id="workpanel" hidden={(!parsed && !learning) || (!learning && (tab === '字幕与转写' || tab === '文件重命名'))} role={page === 'learning' ? 'region' : 'tabpanel'} aria-labelledby={page === 'learning' ? 'page-title-learning' : 'tab-' + tabs.find(item => item.name === tab)?.id} className={`work-grid study-workspace${!fullInspector ? ' compact-context' : ''}${learning && media && studyCollapsed ? ' study-workspace-collapsed' : ''}${managing || (learning && !parsed) ? ' study-single-column' : ''}`}>
            <div className="study-main-column">
              <section className="preview-panel" aria-labelledby="content-heading" hidden={managing || !parsed}>
                <h2 id="content-heading" className="sr-only">项目内容</h2>
                <MediaStage projectId={parsed?.project.id} title={parsed?.title} coverURL={parsed?.cover_url}
                  media={media} videoRef={videoRef} coverError={coverError} onCoverError={() => setCoverError(true)}
                  onVideoError={() => setVideoError(true)} preparing={busy === 'prepare'} failed={archiveProblem}
                  message={selected?.status || ''} task={selected?.task} cancelDisabled={cancelDisabled}
                  retryDisabled={busy !== null || !quality} onCancel={() => { void cancelWorkflow(); }} onRetry={() => { void run('prepare'); }} />
                {videoError && <p className="error-text" role="alert">浏览器无法播放，请在归档目录中用本地播放器打开。</p>}
                {/* Resume is a player concern, so it also shows on the workspace tab, not only in the study view. */}
                {parsed && media && <PlaybackResume key={`${parsed.project.id}:${media.resource}`}
                  projectId={parsed.project.id} media={media} videoRef={videoRef} onSeek={seekVideo} anchorRef={resumeBar}
                  showEmpty={learning} />}
                <div hidden={!learning}>
                {media && currentTimeline && <ChapterTimeline timeline={currentTimeline} view="player" compact
                  videoRef={videoRef} mediaKey={`${parsed?.project.id}:${media.preview_url}`} duration={media.duration}
                  enabled={timelineEnabled} onSeek={seekVideo} />}
                </div>
              </section>
              <section className="learning-notes-panel" aria-label={learning ? 'AI 学习材料' : 'AI 分析与材料管理'} hidden={activeTab !== 'AI 分析'}>
                <AnalysisPanel learning={learning} onManage={openAnalysis} onPrepare={openPreparation} onUseForLearning={openLearning} onTimelineChange={setAnalysisTimeline} onChatChange={setLearningChat} parsed={parsed} media={media} qualityId={quality} config={config} busy={busy} status={selected?.status || ''} error={!!selected?.error}
                  task={selected?.task} version={selected?.analysisVersion || 0} lastRecord={selected?.lastRecord} cancelDisabled={cancelDisabled}
                  onCancel={() => { void cancelWorkflow(); }} onAnalyze={body => { void run('analyze', { kind: 'analyze', body }); }}
                  onAsk={(recordId, question, position, agent, images) => { void run('analyze', { kind: 'ask', recordId, question, position, agent, images }); }}
                  onSeek={media ? seconds => { if (!learning) navigate('learning'); seekVideo(seconds); } : undefined} onOpenSettings={button => openSettings(button, true)} onGenerateSubtitles={generateSubtitles} />
              </section>
            </div>
            <div className="study-context-rail" hidden={managing || (learning && !parsed)}>
              <StudySplitter />
              <div hidden={!learning}>
              {parsed && media && <StudySidebar projectId={parsed.project.id} media={media} videoRef={videoRef}
                timeline={currentTimeline} enabled={timelineEnabled} collapsed={studyCollapsed}
                alignTo={resumeBar} active={learning}
                subtitles={{ projectId: parsed.project.id, mediaKey: media.resource, videoRef, onSeek: seekVideo, onGenerate: generateSubtitles,
                  version: selected?.analysisVersion }}
                onToggle={() => setStudyCollapsed(value => !value)} onSeek={seekVideo}
                assistant={{ chat: currentChat, busy, status: selected?.status || '', error: !!selected?.error, task: selected?.task, cancelDisabled,
                  onAsk: (recordId, question, position, agent, images) => { void run('analyze', { kind: 'ask', recordId, question, position, agent, images }); },
                  onCancel: () => { void cancelWorkflow(); }, onOpenSettings: button => openSettings(button, true), onManage: openAnalysis, onGenerateSubtitles: generateSubtitles }} />}
              </div>
            <Panel as="aside" id="project-context" hidden={!parsed || (learning && ((!!media && studyCollapsed) || (parsed?.episodes?.length || 0) <= 1))} className={'details-panel' + (!fullInspector ? ' compact-inspector' : '')} aria-labelledby={learning ? undefined : 'details-heading'} aria-label={learning ? '分集学习上下文' : undefined}>
          {!learning && <div className="project-context-heading"><PanelTitle secondary id="details-heading">{fullInspector ? '项目与归档' : '当前项目'}</PanelTitle></div>}
          <div className="inspector-identity" hidden={learning}>
          <h3 className="info-title" translate={parsed?.title ? 'no' : undefined}>{videoTitle || '尚未选择视频'}
          </h3>{parsed && projectTitle !== videoTitle && <p className="muted" translate="no">{projectTitle}</p>}{parsed && <div className="identity-meta"><span className="platform-mark">{parsed.platform}{!fullInspector && parsed.project.part ? ` · P${parsed.project.part}` : ''}
          </span><a className="source-link" href={parsed.source_url} target="_blank" rel="noreferrer" aria-label="打开原始链接" title="打开原始链接"><Icon name="external-link" /></a></div>}
          </div>
              {fullInspector && parsed && <>
          <dl className="media-meta">
          <div>
          <dt>视频时长</dt>
          <dd>{media ? formatDuration(media.duration) : platformDuration ? formatDuration(platformDuration) : '—'}
          </dd>
          </div>
          <div>
          <dt>分集</dt>
          <dd>{parsed?.project.part ? 'P' + parsed.project.part : '—'}
          </dd>
          </div>
          <div>
          <dt>分辨率</dt>
          <dd>{media?.width && media.height ? `${media.width} × ${media.height}` : '—'}
          </dd>
          </div>
          <div>
          <dt>实际大小</dt>
          <dd>{media ? (media.size_bytes / 1024 / 1024).toFixed(1) + ' MB' : '—'}
          </dd>
          </div>
          </dl>
          <FormField className="info-field quality-field" labelClassName="field-label" htmlFor="video-quality" label="归档画质">
          <select id="video-quality" disabled={!parsed || !parsed.qualities.length || busy !== null} value={quality} onChange={event => chooseQuality(event.target.value)}>{!parsed?.qualities.length && <option value="">解析后选择画质</option>}
          {parsed?.qualities.map(item => <option key={item.id} value={item.id}>{item.label}
          {item.media && !item.label.includes('已归档') ? ' · 已归档' : ''}
          </option>)}
          </select>
          </FormField>
          </>}
              {fullInspector && <>
          {parsed && !media && busy !== 'prepare' && !archiveProblem && <button className="primary download-button" type="button" disabled={!quality || busy !== null} onClick={() => { void run('prepare'); }}>
          <Icon name="download" />{fullInspector ? '下载并归档' : '先归档视频'}
          </button>}
          {busy !== 'prepare' && !archiveProblem && <div className={`status inset ${selected?.error ? 'status-error' : ''}`} role="status" aria-live="polite" aria-atomic="true">{showStatus ? inspectorStatus : ''}</div>}
          </>}
              {fullInspector && parsed && <>
          <ProjectPanel key={parsed.project.id} project={parsed.project} onRenamed={renameProject} />
          {qualityLimited && ['missing', 'missing_session', 'expired', 'invalid'].includes(parsed.bilibili_cookie_status || '') && <p className="notice quality-login">{qualityLogin === 'done' ? '已登录 B站，请重新解析链接以获取更高画质。' : <>画质受限：没有登录 B站，最高只有 480p。<button type="button" className="link-button" onClick={() => setQualityLogin('open')}>扫码登录</button><button type="button" className="link-button" onClick={() => openAccounts('bilibili')}>账号与登录</button></>}</p>}
          {qualityLogin === 'open' && <BiliLogin onClose={() => setQualityLogin('closed')} onSuccess={() => setQualityLogin('done')} />}
          </>}
              {parsed?.platform === '哔哩哔哩' && <div className="episode-refresh"><button type="button" disabled={refreshingEpisodes || busy !== null} onClick={() => { void refreshEpisodes(); }}>{refreshingEpisodes ? '解析分集中…' : '解析视频集数'}</button><p role="status">{episodeMessage}</p>
                {collection && <div className="collection-list" aria-label={`合集《${collection.title}》`}>
                  <ol>{collection.items.map(item => <li key={item.bvid}>
                    <button type="button" aria-current={item.current || undefined} disabled={item.current || busy !== null || parsing}
                      onClick={() => importCollectionItem(item.bvid)} title={item.current ? '正在查看这个视频' : '解析并打开这个视频'}>
                      <span>{item.title}</span>
                      <small>{item.duration ? formatDuration(item.duration) : ''}{item.current ? ' · 当前' : ''}</small>
                    </button></li>)}</ol>
                </div>}
              </div>}
              {learning && episodeLearning}
            </Panel>
            {!learning && episodeLearning && <Panel as="aside" className="episode-learning-card" aria-label="分集学习">
              {episodeLearning}
            </Panel>}
            </div>
          </div>
        <section id="subtitle-workpanel" role="tabpanel" aria-labelledby="tab-3" hidden={learning || tab !== '字幕与转写'}>
          <div className="transcription-layout">
          <Transcription config={config} onOpenSettings={button => openSettings(button, true)} projectId={parsed?.project.id} media={media} ready={config?.transcription_ready} subtitlesReady={config?.subtitles_ready}
            uploadLimit={config?.transcription_upload_limit} reuseModel={settings.reuseTranscriptionModel} focusRequest={subtitleFocus}
            visible={page === 'workspace' && tab === '字幕与转写'} onSelectSource={selectSubtitleSource}
            pending={parsed && parsed.platform === '哔哩哔哩' ? { source_url: parsed.source_url, title: parsed.title, platform: parsed.platform, part: parsed.project.part ?? null, project_id: parsed.project.id } : null} />
          </div>
        </section>
        <section id="naming-workpanel" role="tabpanel" aria-labelledby="tab-4" hidden={learning || tab !== '文件重命名'}>
          {parsed ? namingVisited && <SubtitleNaming key={parsed.project.id} projectId={parsed.project.id} config={config}
            visible={page === 'workspace' && tab === '文件重命名'} initialTaskId={namingTask?.projectId === parsed.project.id ? namingTask.id : null}
            onOpenSettings={button => openSettings(button, true)} onBeforeApply={() => videoRef.current?.pause()}
            onChanged={async () => { setLibraryVersion(value => value + 1); const id = selectedRef.current; if (id) await restoreProject(id, media?.resource || '', false, true); }} />
            : <Panel className="naming-empty"><h2>文件重命名</h2><p>请先选择视频项目，再为已归档且有字幕的分集命名。</p>
              </Panel>}
        </section>
        </WorkbenchPage>
        <WorkbenchPage page="tasks" activePage={page}>
          <TaskSemanticSearch visible={page === 'tasks'} version={libraryVersion} currentProjectId={parsed?.project.id} onSeek={async (id, time) => {
            videoRef.current?.pause();
            const restored = await restoreProject(id, '', false);
            if (!restored) throw new Error('无法打开检索结果对应的视频，请刷新项目列表。');
            navigate('learning', true); requestAnimationFrame(() => seekVideo(time));
          }} />
          <TaskCenter onActivityChange={setActiveCount} onOpenWorkspace={() => navigate('workspace', true)} />
          </WorkbenchPage>
        <WorkbenchPage page="accounts" activePage={page}>
          <Accounts visible={page === 'accounts'} focusPlatform={accountFocus} />
          </WorkbenchPage>
        <WorkbenchPage page="settings" activePage={page}>
          {(page === 'settings' || settingsVisited) && <SettingsPage settings={settings} onChange={saveSettings} config={config}
            configError={configError} saveStatus={settingsSaveStatus} activeCount={activeCount} onAIChange={updateAI}
            onArchiveRootChange={updateArchiveRoot} visible={page === 'settings'} focusAIRequest={settingsFocusRequest} focusAITarget={settingsFocusTarget} />}
        </WorkbenchPage>
  </WorkbenchLayout>;
}
