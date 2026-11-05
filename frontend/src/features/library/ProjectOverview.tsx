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

function actionFor(project: VideoProjectSummary): { label: string; target: OpenTarget } {
  if (!project.archived) return { label: '去归档', target: 'workspace' };
  if (!subtitleCount(project)) return { label: '补字幕', target: 'subtitles' };
  if (!analysisCount(project)) return { label: '做 AI 分析', target: 'learning' };
  return { label: '查看材料', target: 'learning' };
}

export default function ProjectOverview({ visible, version = 0, currentId, onOpen }: Props) {
  const [projects, setProjects] = useState<VideoProjectSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [reload, setReload] = useState(0);
  const [openingId, setOpeningId] = useState<string | null>(null);

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
    archived: projects.filter(project => project.archived).length,
    subtitles: projects.filter(project => subtitleCount(project) > 0).length,
    analyzed: projects.filter(project => analysisCount(project) > 0).length,
    attention: projects.filter(project => !project.archived || !subtitleCount(project) || !analysisCount(project)).length,
  }), [projects]);

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

  return <section className="video-overview" aria-labelledby="video-overview-title">
    <header className="overview-intro">
      <div>
        <p className="overview-eyebrow">资料状态</p>
        <h2 id="video-overview-title">所有视频，一张表看清</h2>
        <p>同时核对视频归档、字幕、AI 分析和已经生成的学习材料。</p>
      </div>
      <button type="button" className="overview-refresh" disabled={loading} aria-busy={loading}
        onClick={() => setReload(value => value + 1)}><Icon name="loader-circle" />{loading ? '刷新中…' : '刷新'}</button>
    </header>

    <div className="overview-metrics" aria-label="视频资料汇总">
      <div><span>已归档视频</span><strong>{metrics.archived}</strong><small>共 {projects.length} 个视频项目</small></div>
      <div><span>已有字幕</span><strong>{metrics.subtitles}</strong><small>平台字幕或本地转写</small></div>
      <div><span>已有 AI 材料</span><strong>{metrics.analyzed}</strong><small>至少生成过一份分析</small></div>
      <div><span>待补齐</span><strong>{metrics.attention}</strong><small>缺归档、字幕或分析</small></div>
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

    {error && <p className="overview-error" role="alert">{error}<button type="button" onClick={() => setReload(value => value + 1)}>重试</button></p>}
    {loading && projects.length === 0 && <p className="overview-loading" role="status">正在汇总视频资料…</p>}
    {!loading && !error && projects.length === 0 && <div className="overview-empty">
      <Icon name="layout-dashboard" /><strong>还没有视频项目</strong><p>先到视频工作台解析并归档一个视频，资料状态会显示在这里。</p>
    </div>}
    {!loading && projects.length > 0 && filtered.length === 0 && <p className="overview-loading" role="status">没有匹配的视频。可以清除搜索或切换筛选条件。</p>}

    {filtered.length > 0 && <div className="overview-table-wrap" aria-busy={loading}>
      <table className="overview-table">
        <caption className="sr-only">所有视频的字幕、AI 分析和材料状态</caption>
        <thead><tr><th scope="col">视频</th><th scope="col">字幕</th><th scope="col">AI 分析</th><th scope="col">已生成材料</th><th scope="col">操作</th></tr></thead>
        <tbody>{filtered.map(project => {
          const subs = subtitleCount(project), analyses = analysisCount(project), groups = materialGroups(project);
          const action = actionFor(project), current = currentId === project.id;
          return <tr key={project.id} className={current ? 'is-current' : undefined}>
            <th scope="row" data-label="视频">
              <span className="overview-video-title" translate="no" title={projectDisplayTitle(project)}>{videoDisplayTitle(project)}</span>
              <span className="overview-video-meta">
                <span>{project.platform}</span>{project.part ? <span>P{project.part}</span> : null}
                <span className={project.archived ? 'is-ready' : 'is-missing'}>{project.archived ? '已归档' : '未归档'}</span>
                {project.learned ? <span>已学完</span> : null}{current ? <span>当前打开</span> : null}
              </span>
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
  </section>;
}