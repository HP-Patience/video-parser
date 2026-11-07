import { useEffect, useRef, useState } from 'react';
import Panel from '../../components/Panel';
import type { VideoProjectSummary } from '../../lib/api';
import { cachedProjects, loadProjects } from '../../lib/projectCatalog';
import SemanticSearch from '../learning/SemanticSearch';

type Props = { visible: boolean; version?: number; currentProjectId?: string; onSeek: (projectId: string, time: number) => Promise<void> };
export default function TaskSemanticSearch({ visible, version = 0, currentProjectId, onSeek }: Props) {
  const [projects, setProjects] = useState<VideoProjectSummary[]>(() => cachedProjects(version) || []);
  const [selected, setSelected] = useState('');
  const [reload, setReload] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(() => visible && !cachedProjects(version));
  const [opening, setOpening] = useState(false);
  const consumed = useRef(0);
  useEffect(() => {
    if (!visible) return;
    const refresh = reload !== consumed.current;
    if (refresh) consumed.current = reload;
    const ready = cachedProjects(version);
    if (ready && !refresh) { setProjects(ready); setLoading(false); setError(''); return; }
    let active = true;
    setLoading(true); setError('');
    loadProjects(version, refresh).then(data => { if (active) setProjects(data); })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '无法读取项目列表。'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [visible, version, reload]);
  // The selection follows the list and the current video; it must not refetch the list.
  useEffect(() => {
    setSelected(old => projects.some(item => item.id === old) ? old : projects.some(item => item.id === currentProjectId) ? currentProjectId! : '');
  }, [projects, currentProjectId]);
  return <Panel className="task-semantic-search" aria-label="字幕语义检索">
    <h2>字幕语义检索（Embedding）</h2>
    <div className="semantic-project-controls"><label>检索项目<select aria-label="检索项目" value={selected} disabled={loading || opening} onChange={event => { setSelected(event.target.value); setError(''); }}>
      <option value="">选择已归档视频…</option>
      {projects.map(item => <option key={item.id} value={item.id}>{[item.episode_title || item.title, item.part ? `P${item.part}` : ''].filter(Boolean).join(' · ')}</option>)}
    </select></label><button type="button" disabled={loading || opening} onClick={() => setReload(value => value + 1)}>刷新项目列表</button></div>
    {/* A permanent slot: showing and then removing this line used to shift the whole page. */}
    <p className={'semantic-project-status' + (error ? ' error-text' : '')} role={error ? 'alert' : 'status'}>{error || (loading ? '正在读取项目列表…' : '')}</p>
    {selected ? <SemanticSearch key={selected} projectId={selected} onSeek={time => {
      if (opening) return;
      setOpening(true); setError('');
      void onSeek(selected, time).catch(reason => setError(reason instanceof Error ? reason.message : '无法打开视频。')).finally(() => setOpening(false));
    }} /> : <p>请选择要检索字幕的已归档视频，再建立索引或输入问题。</p>}
  </Panel>;
}
