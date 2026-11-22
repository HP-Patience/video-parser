import { useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import Icon from '../../components/Icon';
import ProjectTitle from '../../components/ProjectTitle';
import { request } from '../../lib/api';
import type { ParsedVideo, VideoProject, VideoProjectSummary } from '../../lib/api';
import { formatClock, formatMegabytes } from '../../lib/format';
import { projectDisplayTitle, videoDisplayTitle } from '../../lib/projectTitles';
import './project-overview.css';

type OpenTarget = 'workspace' | 'subtitles' | 'learning';
type Filter = 'all' | 'missing-subtitles' | 'missing-analysis';

/**
 * List ↔ grid morph. Every project keeps one named box across both views, so the browser snapshots
 * it, swaps the layout, and tweens the snapshot to the new position instead of cross-fading a new
 * list. Same idea as a FLIP animation, done by the platform; unsupported browsers just swap.
 */
type MorphDocument = Document & { startViewTransition?: (update: () => void) => { finished: Promise<void> } };

function morphAvailable() {
  return typeof document !== 'undefined' && typeof (document as MorphDocument).startViewTransition === 'function'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Names must be unique per project and stable across views; the id is a hex digest. */
const morphName = (id: string) => `overview-video-${id}`;

type Props = {
  visible: boolean;
  version?: number;
  currentId?: string | null;
  onOpen: (project: VideoProjectSummary, target: OpenTarget) => Promise<void>;
  /** Files were renamed in place: reload the cards and let the page re-read the open project. */
  onRenamed?: (project: VideoProject) => void;
};

const subtitleCount = (project: VideoProjectSummary) => project.subtitle_count ?? (project.has_subtitles ? 1 : 0);
const analysisCount = (project: VideoProjectSummary) => project.analysis_count ?? project.materials?.length ?? 0;
const hasMaterials = (project: VideoProjectSummary) => subtitleCount(project) > 0 || analysisCount(project) > 0;
const isLocalProject = (project: VideoProjectSummary) => project.source_kind === 'local';
const hasCloudMaterials = (project: VideoProjectSummary) => (project.cloud_material_count || 0) > 0 || hasMaterials(project);
const videoAvailabilityLabel = (project: VideoProjectSummary) => {
  if (project.archived || project.has_local_video) return '已归档';
  return isLocalProject(project) ? '本地视频未关联' : '未归档';
};
const completionCount = (project: VideoProjectSummary) => Number(project.archived) + Number(subtitleCount(project) > 0) + Number(analysisCount(project) > 0);
const completionPercent = (project: VideoProjectSummary) => Math.round(completionCount(project) / 3 * 100);
const materialGroups = (project: VideoProjectSummary) => {
  const groups = new Map<string, { name: string; count: number }>();
  for (const material of project.materials || []) {
    const current = groups.get(material.name);
    if (current) current.count += 1;
    else groups.set(material.name, { name: material.name, count: 1 });
  }
  return [...groups.values()];
};
const searchText = (project: VideoProjectSummary) => [
  projectDisplayTitle(project), videoDisplayTitle(project), project.platform, project.episode_title || '', project.location,
].join(' ').toLocaleLowerCase();
const dateLabel = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '日期未知' : new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(date);
};
const statusLabel = (project: VideoProjectSummary) => {
  if (!project.archived && isLocalProject(project)) return videoAvailabilityLabel(project);
  if (!project.archived) return '未归档';
  if (!subtitleCount(project)) return '缺字幕';
  if (!analysisCount(project)) return '待分析';
  return '资料完整';
};

function actionFor(project: VideoProjectSummary): { label: string; target: OpenTarget } {
  if (!(project.archived || project.has_local_video) && !hasCloudMaterials(project)) return { label: isLocalProject(project) ? '关联本地视频' : '去归档', target: 'workspace' };
  if (!subtitleCount(project)) return { label: '补字幕', target: 'subtitles' };
  if (!analysisCount(project)) return { label: '做 AI 分析', target: 'learning' };
  return { label: '查看材料', target: 'learning' };
}

function ProjectCover({ url }: { url?: string | null }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  return <span className="overview-cover">
    {url && failedUrl !== url ? <img src={url} alt="" loading="lazy" decoding="async" onError={() => setFailedUrl(url)} />
      : <span className="overview-cover-placeholder"><Icon name="image" /><span>暂无封面</span></span>}
  </span>;
}

export default function ProjectOverview({ visible, version = 0, currentId, onOpen, onRenamed }: Props) {
  const [projects, setProjects] = useState<VideoProjectSummary[]>([]);
  // Start in the loading state, so the first paint is the skeleton instead of the empty state.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [view, setView] = useState<'list' | 'grid'>('grid');
  const [reload, setReload] = useState(0);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [bindingId, setBindingId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    request<{ projects: VideoProjectSummary[] }>('/projects', controller.signal)
      .then(result => { if (!controller.signal.aborted) setProjects(result.projects); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取视频总览。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [visible, version, reload]);

  // Content rises in once, when the first payload lands; the skeleton already holds the layout.
  const [revealed, setRevealed] = useState(false);
  const hadProjects = useRef(false);
  useEffect(() => {
    if (!projects.length) { hadProjects.current = false; return; }
    if (hadProjects.current) return;
    hadProjects.current = true;
    setRevealed(true);
    const timer = window.setTimeout(() => setRevealed(false), 420);
    return () => window.clearTimeout(timer);
  }, [projects.length]);

  const metrics = useMemo(() => ({
    subtitles: projects.filter(project => subtitleCount(project) > 0).length,
    analyzed: projects.filter(project => analysisCount(project) > 0).length,
    attention: projects.filter(project => completionCount(project) < 3).length,
  }), [projects]);

  const current = useMemo(() => projects.find(project => project.id === (selectedId || currentId)) || projects[0],
    [currentId, projects, selectedId]);

  // Archive facts (duration, pixels, size, quality) live in the project detail, not in the list payload.
  const [detail, setDetail] = useState<ParsedVideo | null>(null);
  useEffect(() => {
    const id = current?.id;
    setDetail(null);
    if (!id) return;
    const controller = new AbortController();
    request<ParsedVideo>(`/projects/${id}/resume`, controller.signal)
      .then(value => { if (!controller.signal.aborted) setDetail(value); })
      .catch(() => { /* The card falls back to dashes when the detail is unavailable. */ });
    return () => controller.abort();
  }, [current?.id, version, reload]);
  const attention = useMemo(() => projects
    .filter(project => completionCount(project) < 3)
    .sort((a, b) => completionCount(a) - completionCount(b))
    .slice(0, 4), [projects]);

  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return projects.filter(project => {
      if (filter === 'missing-subtitles' && subtitleCount(project) > 0) return false;
      if (filter === 'missing-analysis' && analysisCount(project) > 0) return false;
      return !term || searchText(project).includes(term);
    });
  }, [filter, projects, query]);

  async function bindLocalVideo(project: VideoProjectSummary) {
    setSelectedId(project.id);
    setBindingId(project.id);
    fileInput.current?.click();
  }

  /** Swap views inside a view transition, so each named card travels to its new box. */
  function changeView(next: 'list' | 'grid') {
    if (next === view) return;
    if (!morphAvailable()) { setView(next); return; }
    (document as MorphDocument).startViewTransition!(() => { flushSync(() => setView(next)); });
  }

  async function onVideoPicked(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    const projectId = bindingId;
    setBindingId(null);
    const path = file && (file as File & { path?: string }).path;
    if (!projectId || !file) return;
    if (!path) { setError('当前浏览器未提供本机路径，请使用桌面版文件选择器。'); return; }
    try {
      const controller = new AbortController();
      const task = await request<{ task_id: string }>('/local-media/start', controller.signal, { project_id: projectId, path });
      let state = 'queued';
      while (!['completed', 'failed', 'cancelled'].includes(state)) {
        await new Promise(resolve => window.setTimeout(resolve, 500));
        const result = await request<{ state: string; message?: string }>(`/local-media/tasks/${task.task_id}`, controller.signal);
        state = result.state;
        if (state === 'failed' || state === 'cancelled') throw new Error(result.message || '本地视频关联失败。');
      }
      setReload(value => value + 1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '本地视频关联失败。'); }
  }

  async function open(project: VideoProjectSummary) {
    if (openingId) return;
    if (isLocalProject(project) && !project.archived && !project.has_local_video) {
      bindLocalVideo(project);
      return;
    }
    const action = actionFor(project);
    setSelectedId(project.id);
    setOpeningId(project.id);
    setError('');
    try {
      await onOpen(project, action.target);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法打开这个视频。');
    } finally {
      setOpeningId(null);
    }
  }

  const currentAction = current ? actionFor(current) : null;
  const currentSteps = current ? [
    { label: '视频归档', done: current.archived },
    { label: '字幕资料', done: subtitleCount(current) > 0 },
    { label: 'AI 分析', done: analysisCount(current) > 0 },
  ] : [];
  const archivedMedia = detail?.media || null;
  const archivedQuality = detail?.qualities.find(item => item.media) || null;
  const archivedDuration = archivedMedia?.duration ?? detail?.duration ?? null;

  return <section className="video-overview" aria-labelledby="video-overview-title">
    <h2 id="video-overview-title" className="sr-only">视频总览</h2>

    {error && <p className="overview-error" role="alert">{error}<button type="button" onClick={() => setReload(value => value + 1)}>重试</button></p>}
    <input ref={fileInput} type="file" accept="video/*,.mkv,.ts,.mts,.m2ts" hidden onChange={event => { void onVideoPicked(event); }} />
    {/* Placeholders hold the real geometry, so the cards resolve in place instead of popping in. */}
    {loading && projects.length === 0 && <div className="overview-skeleton">
      <p className="sr-only" role="status">正在汇总视频资料…</p>
      <div className="overview-bento" aria-hidden="true">
        <article className="overview-card overview-current-card">
          <span className="sk-line sk-kicker" />
          <span className="sk-line sk-headline" />
          <span className="sk-line sk-headline sk-short" />
          <div className="overview-current-body">
            <div className="overview-current-visual"><span className="sk-line sk-cover" /></div>
            <div className="overview-progress-list">
              {[0, 1, 2].map(row => <div className="overview-progress-row" key={row}>
                <span className="sk-line sk-label" />
                <span className="sk-line sk-label-end" />
                <span className="sk-line sk-bar" />
              </div>)}
            </div>
          </div>
          <div className="overview-current-archive">
            <div className="overview-current-facts">
              {[0, 1, 2, 3, 4].map(cell => <div key={cell}>
                <span className="sk-line sk-tiny" />
                <span className="sk-line sk-value" />
              </div>)}
            </div>
            <span className="sk-line sk-origin" />
          </div>
          <div className="overview-current-actions"><span className="sk-line sk-button" /></div>
        </article>
        <article className="overview-card overview-attention-card">
          <span className="sk-line sk-kicker" />
          <ul className="overview-attention-list">
            {[0, 1, 2, 3].map(row => <li className="overview-sk-row" key={row}>
              <span className="sk-line sk-dot" />
              <span className="overview-attention-copy">
                <span className="sk-line sk-row-title" />
                <span className="sk-line sk-tiny" />
              </span>
            </li>)}
          </ul>
        </article>
      </div>
      <section className="overview-library" aria-hidden="true">
        <div className="overview-library-heading">
          <span className="sk-line sk-kicker" />
          <span className="sk-line sk-section-title" />
        </div>
        <div className="overview-toolbar">
          <span className="sk-line sk-search" />
          <span className="overview-filters">
            {[0, 1, 2].map(filter => <span className="sk-line sk-filter" key={filter} />)}
          </span>
        </div>
        <ul className="overview-grid">
          {[0, 1, 2, 3].map(card => <li className="overview-video-card" key={card}>
            <span className="sk-line sk-card-cover" />
            <div className="overview-card-copy">
              <span className="sk-line sk-row-title" />
              <span className="sk-line sk-tiny" />
            </div>
          </li>)}
        </ul>
      </section>
    </div>}
    {!loading && !error && projects.length === 0 && <div className="overview-empty">
      <span className="overview-empty-icon"><Icon name="layout-dashboard" /></span>
      <strong>还没有视频项目</strong><p>先到视频工作台解析并归档一个视频，资料状态会显示在这里。</p>
    </div>}

    {projects.length > 0 && <>
      <div className={'overview-bento' + (revealed ? ' is-revealed' : '')}>
        {current && <article className="overview-card overview-current-card" aria-labelledby="overview-current-title">
          <div className="overview-card-topline">
            <div>
              <p className="overview-card-kicker">视频信息</p>
            </div>
          </div>

          <div className="overview-current-heading" aria-live="polite" aria-atomic="true">
            <ProjectTitle as="h3" id="overview-current-title" text={videoDisplayTitle(current)} full={projectDisplayTitle(current)}
              projectId={current.id} disabled={openingId !== null}
              onRenamed={project => { setReload(value => value + 1); onRenamed?.(project); }} />
            <span className="overview-completion-value">{completionPercent(current)}%</span>
          </div>

          <div className="overview-current-body">
            <div className="overview-current-visual" aria-hidden="true">
              <ProjectCover key={current.id} url={current.cover_url} />
            </div>

            <div className="overview-progress-list" aria-label="当前项目资料进度">
              {currentSteps.map(step => <div className="overview-progress-row" key={step.label}>
                <span>{step.label}</span>
                <strong>{step.done ? '已完成' : '待补齐'}</strong>
                <div className="overview-progress-track" role="progressbar" aria-label={step.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={step.done ? 100 : 0}>
                  <i style={{ width: step.done ? '100%' : '7%' }} />
                </div>
              </div>)}
            </div>
          </div>

          <div className="overview-current-archive">
            <dl className="overview-current-facts">
              <div><dt>视频时长</dt><dd>{archivedDuration ? formatClock(Math.round(archivedDuration)) : '—'}</dd></div>
              <div><dt>分集</dt><dd>{current.part ? `P${current.part}` : '—'}</dd></div>
              <div><dt>分辨率</dt><dd>{archivedMedia?.width && archivedMedia.height ? `${archivedMedia.width} × ${archivedMedia.height}` : '—'}</dd></div>
              <div><dt>实际大小</dt><dd>{archivedMedia ? formatMegabytes(archivedMedia.size_bytes) : '—'}</dd></div>
              <div><dt>归档画质</dt><dd>{archivedQuality ? `${archivedQuality.label}${archivedQuality.label.includes('已归档') ? '' : ' · 已归档'}` : '未归档'}</dd></div>
            </dl>
            <p className="overview-current-origin">
              {detail?.source_url
                ? <a className="overview-origin-platform" href={detail.source_url} target="_blank" rel="noreferrer"
                  aria-label="打开原始链接" title="打开原始链接">{current.platform}<Icon name="external-link" /></a>
                : <span className="overview-origin-platform">{current.platform}</span>}
              <span>{dateLabel(current.created_at)}</span>
            </p>
          </div>

          <div className="overview-current-actions">
            <button type="button" className="overview-primary-action" disabled={openingId !== null} aria-busy={openingId === current.id}
              onClick={() => { void open(current); }}>{openingId === current.id ? '打开中…' : currentAction?.label}<span aria-hidden="true">→</span></button>
          </div>
        </article>}

        <article className="overview-card overview-attention-card" aria-labelledby="overview-attention-title">
          <div className="overview-card-heading">
            <p className="overview-card-kicker" id="overview-attention-title">待办任务</p>
          </div>
          {attention.length ? <div className="overview-attention-list">{attention.map((project, index) => {
            const action = actionFor(project);
            return <button key={project.id} type="button" className="overview-attention-row" disabled={openingId !== null}
              aria-label={`${videoDisplayTitle(project)}：${action.label}`} onClick={() => { void open(project); }}>
              <span className="overview-attention-index">{String(index + 1).padStart(2, '0')}</span>
              <span className="overview-attention-copy">
                <ProjectTitle as="strong" text={videoDisplayTitle(project)} full={projectDisplayTitle(project)} />
                <small>{statusLabel(project)}</small>
              </span>
              <span className="overview-row-arrow" aria-hidden="true">↗</span>
            </button>;
          })}</div> : <div className="overview-all-clear">
            <span className="overview-all-clear-icon"><Icon name="book-open" /></span>
            <strong>资料链已经完整</strong>
            <p>所有项目都具备归档、字幕和分析材料。</p>
          </div>}
        </article>
      </div>

      <section className={'overview-library' + (revealed ? ' is-revealed' : '')} aria-labelledby="overview-library-title">
        <div className="overview-library-heading">
          <div><h3 id="overview-library-title">全部视频</h3></div>
          <div className="overview-library-controls">
            <span className="overview-muted-count">显示 {filtered.length} / {projects.length} 项</span>
            <div className="overview-view-switch" role="group" aria-label="视频展示方式">
              <button type="button" aria-pressed={view === 'list'} onClick={() => changeView('list')}><Icon name="menu" />列表</button>
              <button type="button" aria-pressed={view === 'grid'} onClick={() => changeView('grid')}><Icon name="layout-dashboard" />网格</button>
            </div>
            <button type="button" className="overview-refresh" disabled={loading} aria-busy={loading}
              onClick={() => setReload(value => value + 1)}><Icon name="loader-circle" />{loading ? '刷新中…' : '刷新资料'}</button>
          </div>
        </div>

        <div className="overview-toolbar">
          <label className="overview-search">
            <span className="sr-only">搜索视频</span>
            <Icon name="scan-eye" />
            <input type="search" value={query} placeholder="搜索标题、平台或分集" onChange={event => setQuery(event.target.value)} />
            {query && <button type="button" aria-label="清除搜索" onClick={() => setQuery('')}><Icon name="x" /></button>}
          </label>
          <div className="overview-filters" role="group" aria-label="筛选视频">
            <button type="button" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>全部 <b>{projects.length}</b></button>
            <button type="button" aria-pressed={filter === 'missing-subtitles'} onClick={() => setFilter('missing-subtitles')}>缺字幕 <b>{projects.length - metrics.subtitles}</b></button>
            <button type="button" aria-pressed={filter === 'missing-analysis'} onClick={() => setFilter('missing-analysis')}>无 AI 分析 <b>{projects.length - metrics.analyzed}</b></button>
          </div>
        </div>

        {!loading && projects.length > 0 && filtered.length === 0 && <p className="overview-loading" role="status">没有匹配的视频。可以清除搜索或切换筛选条件。</p>}

        {filtered.length > 0 && view === 'grid' && <ul className="overview-grid" aria-label="视频封面网格" aria-busy={loading}>
          {filtered.map(project => {
            const action = actionFor(project), isSelected = current?.id === project.id;
            const subs = subtitleCount(project), analyses = analysisCount(project);
            return <li key={project.id} className={`overview-video-card ${isSelected ? 'is-current' : ''}`}
              style={{ viewTransitionName: morphName(project.id) }}>
              <button type="button" className="overview-card-select" aria-pressed={isSelected}
                aria-label={`在上方预览 ${videoDisplayTitle(project)}`} onClick={() => setSelectedId(project.id)}>
                <ProjectCover url={project.cover_url} />
                <span className="overview-card-copy">
                  <ProjectTitle className="overview-video-title" text={videoDisplayTitle(project)} full={projectDisplayTitle(project)} />
                  <span className="overview-video-meta"><span>{project.platform}</span>{project.part ? <span>P{project.part}</span> : null}
                    <span>{videoAvailabilityLabel(project)}</span>{project.learned && <span>已学完</span>}</span>
                </span>
              </button>
              <div className="overview-card-footer">
                <div className="overview-card-states">
                  <span className={`overview-state ${subs ? 'is-ready' : 'is-missing'}`}>{subs ? `字幕 ${subs} 份` : '无字幕'}</span>
                  <span className={`overview-state ${analyses ? 'is-ready' : 'is-missing'}`}>{analyses ? `分析 ${analyses} 次` : '未分析'}</span>
                </div>
                <button type="button" className="overview-open" disabled={openingId !== null} aria-busy={openingId === project.id}
                  onClick={() => { void open(project); }}>{openingId === project.id ? '打开中…' : action.label}</button>
              </div>
            </li>;
          })}
        </ul>}

        {filtered.length > 0 && view === 'list' && <div className="overview-table-wrap" aria-busy={loading}>
          <table className="overview-table">
            <caption className="sr-only">所有视频的字幕、AI 分析和材料状态</caption>
            <thead><tr><th scope="col" className="overview-cover-head">封面</th><th scope="col">视频</th><th scope="col">字幕</th><th scope="col">AI 分析</th><th scope="col">已生成材料</th><th scope="col">操作</th></tr></thead>
            <tbody>{filtered.map(project => {
              const subs = subtitleCount(project), analyses = analysisCount(project), groups = materialGroups(project);
              const action = actionFor(project), isSelected = current?.id === project.id;
              return <tr key={project.id} className={isSelected ? 'is-current' : undefined}
                style={{ viewTransitionName: morphName(project.id) }}
                onClick={event => {
                  if (!(event.target as HTMLElement).closest('button, a, input, select, textarea')) setSelectedId(project.id);
                }}>
                <td className="overview-cover-cell" data-label="封面"><ProjectCover url={project.cover_url} /></td>
                <th scope="row" data-label="视频">
                  <button type="button" className="overview-select-video" aria-pressed={isSelected}
                    aria-label={`在上方预览 ${videoDisplayTitle(project)}`} onClick={() => setSelectedId(project.id)}>
                    <ProjectTitle className="overview-video-title" text={videoDisplayTitle(project)} full={projectDisplayTitle(project)} />
                    <span className="overview-video-meta">
                      <span>{project.platform}</span>{project.part ? <span>P{project.part}</span> : null}
                      <span className={project.archived ? 'is-ready' : 'is-missing'}>{project.archived ? '已归档' : '未归档'}</span>
                      {project.learned ? <span>已学完</span> : null}
                    </span>
                  </button>
                </th>
                <td data-label="字幕">
                  <span className={`overview-state ${subs ? 'is-ready' : 'is-missing'}`}>{subs ? `已有 ${subs} 份` : '无字幕'}</span>
                  {(project.translation_count || 0) > 0 && <small className="overview-detail">翻译 {project.translation_count} 份</small>}
                </td>
                <td data-label="AI 分析">
                  <span className={`overview-state ${analyses ? 'is-ready' : 'is-missing'}`}>{analyses ? `已分析 ${analyses} 次` : '未分析'}</span>
                </td>
                <td data-label="已生成材料">
                  {groups.length ? <ul className="overview-materials" aria-label="已生成的 AI 材料">{groups.map(group =>
                    <li key={group.name}>{group.name}{group.count > 1 ? ` ×${group.count}` : ''}</li>)}</ul>
                    : <span className="overview-none">未生成</span>}
                </td>
                <td data-label="操作">
                  <button type="button" className="overview-open" disabled={openingId !== null} aria-busy={openingId === project.id}
                    onClick={() => { void open(project); }}>{openingId === project.id ? '打开中…' : action.label}</button>
                </td>
              </tr>;
            })}</tbody>
          </table>
        </div>}
      </section>
    </>}
  </section>;
}
