import { useEffect, useMemo, useState } from 'react';
import Icon from '../../components/Icon';
import { request } from '../../lib/api';
import type { VideoProjectSummary } from '../../lib/api';
import { projectDisplayTitle, videoDisplayTitle } from '../../lib/projectTitles';
import './project-overview.css';

type OpenTarget = 'workspace' | 'subtitles' | 'learning';
type Filter = 'all' | 'missing-subtitles' | 'missing-analysis';

type Props = {
  visible: boolean;
  version?: number;
  currentId?: string | null;
  onOpen: (project: VideoProjectSummary, target: OpenTarget) => Promise<void>;
};

const subtitleCount = (project: VideoProjectSummary) => project.subtitle_count ?? (project.has_subtitles ? 1 : 0);
const analysisCount = (project: VideoProjectSummary) => project.analysis_count ?? project.materials?.length ?? 0;
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
  if (!project.archived) return '未归档';
  if (!subtitleCount(project)) return '缺字幕';
  if (!analysisCount(project)) return '待分析';
  return '资料完整';
};

function actionFor(project: VideoProjectSummary): { label: string; target: OpenTarget } {
  if (!project.archived) return { label: '去归档', target: 'workspace' };
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

export default function ProjectOverview({ visible, version = 0, currentId, onOpen }: Props) {
  const [projects, setProjects] = useState<VideoProjectSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [view, setView] = useState<'list' | 'grid'>('grid');
  const [reload, setReload] = useState(0);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

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

  const metrics = useMemo(() => ({
    subtitles: projects.filter(project => subtitleCount(project) > 0).length,
    analyzed: projects.filter(project => analysisCount(project) > 0).length,
    attention: projects.filter(project => completionCount(project) < 3).length,
  }), [projects]);

  const current = useMemo(() => projects.find(project => project.id === (selectedId || currentId)) || projects[0],
    [currentId, projects, selectedId]);
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

  async function open(project: VideoProjectSummary) {
    if (openingId) return;
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

  return <section className="video-overview" aria-labelledby="video-overview-title">
    <header className="overview-intro">
      <div className="overview-intro-copy">
        <p className="overview-eyebrow">Video library · 本地资料库</p>
        <h2 id="video-overview-title">把视频资料，整理成一张清晰的地图。</h2>
        <p>从归档、字幕到 AI 学习材料，在一个视图里看清每个项目走到了哪一步。</p>
      </div>
      <div className="overview-intro-actions">
        <span className="overview-local-state"><span className="status-dot" />{loading ? '正在同步' : '本地优先'}</span>
        <button type="button" className="overview-refresh" disabled={loading} aria-busy={loading}
          onClick={() => setReload(value => value + 1)}><Icon name="loader-circle" />{loading ? '刷新中…' : '刷新资料'}</button>
      </div>
    </header>

    {error && <p className="overview-error" role="alert">{error}<button type="button" onClick={() => setReload(value => value + 1)}>重试</button></p>}
    {loading && projects.length === 0 && <p className="overview-loading" role="status">正在汇总视频资料…</p>}
    {!loading && !error && projects.length === 0 && <div className="overview-empty">
      <span className="overview-empty-icon"><Icon name="layout-dashboard" /></span>
      <strong>还没有视频项目</strong><p>先到视频工作台解析并归档一个视频，资料状态会显示在这里。</p>
    </div>}

    {projects.length > 0 && <>
      <div className="overview-bento">
        {current && <article className="overview-card overview-current-card" aria-labelledby="overview-current-title">
          <div className="overview-card-topline">
            <div>
              <p className="overview-card-kicker">继续处理</p>
              <span className="overview-project-index">当前选中 · {current.platform}</span>
            </div>
          </div>

          <div className="overview-current-heading" aria-live="polite" aria-atomic="true">
            <h3 id="overview-current-title" translate="no" title={projectDisplayTitle(current)}>{videoDisplayTitle(current)}</h3>
            <span className="overview-completion-value">{completionPercent(current)}%</span>
          </div>
          <p className="overview-current-meta">
            <span>{dateLabel(current.created_at)}</span>{current.part ? <span>P{current.part}</span> : null}
            {current.learned ? <span>已学完</span> : <span>继续学习</span>}
          </p>

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

          <div className="overview-current-actions">
            <button type="button" className="overview-primary-action" disabled={openingId !== null} aria-busy={openingId === current.id}
              onClick={() => { void open(current); }}>{openingId === current.id ? '打开中…' : currentAction?.label}<span aria-hidden="true">→</span></button>
          </div>
        </article>}

        <article className="overview-card overview-attention-card" aria-labelledby="overview-attention-title">
          <div className="overview-card-heading">
            <div><p className="overview-card-kicker">资料提醒</p><h3 id="overview-attention-title">还有哪些空白？</h3></div>
            <span className="overview-round-count">{metrics.attention}</span>
          </div>
          <p className="overview-card-description">优先处理资料链最短的项目，后续学习和检索会更顺。</p>
          {attention.length ? <div className="overview-attention-list">{attention.map((project, index) => {
            const action = actionFor(project);
            return <button key={project.id} type="button" className="overview-attention-row" disabled={openingId !== null}
              aria-label={`${videoDisplayTitle(project)}：${action.label}`} onClick={() => { void open(project); }}>
              <span className="overview-attention-index">{String(index + 1).padStart(2, '0')}</span>
              <span className="overview-attention-copy">
                <strong translate="no" title={projectDisplayTitle(project)}>{videoDisplayTitle(project)}</strong>
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

      <section className="overview-library" aria-labelledby="overview-library-title">
        <div className="overview-library-heading">
          <div><p className="overview-card-kicker">Library</p><h3 id="overview-library-title">全部视频</h3></div>
          <div className="overview-library-controls">
            <span className="overview-muted-count">显示 {filtered.length} / {projects.length} 项</span>
            <div className="overview-view-switch" role="group" aria-label="视频展示方式">
              <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}><Icon name="menu" />列表</button>
              <button type="button" aria-pressed={view === 'grid'} onClick={() => setView('grid')}><Icon name="layout-dashboard" />网格</button>
            </div>
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
            return <li key={project.id} className={`overview-video-card ${isSelected ? 'is-current' : ''}`}>
              <button type="button" className="overview-card-select" aria-pressed={isSelected}
                aria-label={`在上方预览 ${videoDisplayTitle(project)}`} onClick={() => setSelectedId(project.id)}>
                <ProjectCover url={project.cover_url} />
                <span className="overview-card-copy">
                  <span className="overview-video-title" translate="no" title={projectDisplayTitle(project)}>{videoDisplayTitle(project)}</span>
                  <span className="overview-video-meta"><span>{project.platform}</span>{project.part ? <span>P{project.part}</span> : null}
                    <span>{project.archived ? '已归档' : '未归档'}</span>{project.learned && <span>已学完</span>}</span>
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
            <thead><tr><th scope="col">视频</th><th scope="col">字幕</th><th scope="col">AI 分析</th><th scope="col">已生成材料</th><th scope="col">操作</th></tr></thead>
            <tbody>{filtered.map(project => {
              const subs = subtitleCount(project), analyses = analysisCount(project), groups = materialGroups(project);
              const action = actionFor(project), isSelected = current?.id === project.id;
              return <tr key={project.id} className={isSelected ? 'is-current' : undefined}
                onClick={event => {
                  if (!(event.target as HTMLElement).closest('button, a, input, select, textarea')) setSelectedId(project.id);
                }}>
                <th scope="row" data-label="视频">
                  <button type="button" className="overview-select-video" aria-pressed={isSelected}
                    aria-label={`在上方预览 ${videoDisplayTitle(project)}`} onClick={() => setSelectedId(project.id)}>
                    <span className="overview-video-title" translate="no" title={projectDisplayTitle(project)}>{videoDisplayTitle(project)}</span>
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
