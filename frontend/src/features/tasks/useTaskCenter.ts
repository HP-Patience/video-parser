import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import { cancelTask, isActive, type TaskInfo } from '../../lib/jobs';
import useTaskHistory from './useTaskHistory';
import { taskDescription } from './taskDetails';

export const STATUSES = ['all','active','completed','unfinished'] as const;
export function hasStatus(job: TaskInfo, key: string) {
  return key === 'all' || key === 'active' && isActive(job.state) || key === 'completed' && ['completed','empty'].includes(job.state)
    || key === 'unfinished' && ['failed','cancelled','interrupted','partial','login_required'].includes(job.state);
}

export default function useTaskCenter(onActivityChange?: (count: number | null) => void) {
  const { jobs: polled, error, loaded, reload } = useTaskHistory(onActivityChange);
  // Records removed by "清理" are hidden at once instead of waiting for the next 2 s poll.
  const [cleared,setCleared] = useState<string[]>([]);
  const jobs = cleared.length ? polled.filter(job => !cleared.includes(job.id)) : polled;
  const dead = jobs.filter(job => ['failed','cancelled','interrupted'].includes(job.state));
  const [clearing,setClearing] = useState(false);
  const [notice,setNotice] = useState('');
  const [filter,setFilter] = useState('all');
  // Type filter (video / subtitles / transcriptions / analysis), combined with the status filter.
  const [kind,setKind] = useState('all');
  const [query,setQuery] = useState('');
  const [facets, setFacets] = useState({ template: '', engine: '', source: '', model: '', after: '', before: '' });
  const resetFilters = () => { setFilter('all'); setKind('all'); setQuery(''); setFacets({ template: '', engine: '', source: '', model: '', after: '', before: '' }); };
  const [retrying,setRetrying] = useState<string[]>([]);
  const retries = useRef(new Set<string>());
  const [pendingCancels, setPendingCancels] = useState<string[]>([]);
  const cancellations = useRef(new Map<string, AbortController>());
  useEffect(() => () => { for (const controller of cancellations.current.values()) controller.abort(); }, []);
  async function cancel(job:TaskInfo) {
    if (cancellations.current.has(job.id)) return;
    const controller = new AbortController();
    cancellations.current.set(job.id, controller);
    setPendingCancels(current => [...current, job.id]);
    try {
      await cancelTask(job.kind!, job.id, controller.signal);
      if (!controller.signal.aborted) setNotice('已请求停止本任务；等待进程退出后状态会更新。');
    } catch (reason) {
      if (!controller.signal.aborted) setNotice(reason instanceof Error ? reason.message : '取消请求失败，请重试。');
    } finally {
      cancellations.current.delete(job.id);
      if (!controller.signal.aborted) setPendingCancels(current => current.filter(id => id !== job.id));
    }
  }
  // Each facet's counts apply the search and the other facet, so a count always equals what clicking it shows.
  const needle = query.trim().toLowerCase();
  const matched = jobs.filter(job => ['template', 'engine', 'source', 'model'].every(key => !facets[key as keyof typeof facets] || job.details?.[key as 'template'] === facets[key as keyof typeof facets])
    && (!facets.after || !!job.created && job.created * 1000 >= new Date(facets.after + 'T00:00:00').getTime())
    && (!facets.before || !!job.created && job.created * 1000 <= new Date(facets.before + 'T23:59:59.999').getTime()));
  const searched = needle ? matched.filter(job => [...(job.subjects || []).flatMap(item => [item.title, item.part ? 'P' + item.part : '']), ...(job.file_details || []).map(item => item.name), ...(job.video_names || []), ...(job.file_names || []), job.project_title, job.title, job.part ? 'P' + job.part : '', job.id, job.message, taskDescription(job)]
    .some(value => !!value && value.toLowerCase().includes(needle))) : matched;
  const ofKind = (job: TaskInfo, key: string) => key === 'all' || job.kind === key;
  const visible = searched.filter(job => ofKind(job, kind) && hasStatus(job, filter));
  const statusCounts = Object.fromEntries(STATUSES.map(key => [key, searched.filter(job => ofKind(job, kind) && hasStatus(job, key)).length]));
  const kindCount = (key: string) => searched.filter(job => ofKind(job, key) && hasStatus(job, filter)).length;
  async function clearDead() {
    if (clearing) return;
    const ids = dead.map(job => job.id);
    setClearing(true);
    try {
      const result = await request<{ removed: number }>('/tasks/clear', new AbortController().signal, { confirm: true });
      setCleared(current => [...current, ...ids]);
      setNotice(`已清理 ${result.removed} 条任务记录。`);
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : '清理失败，请重试。'); }
    finally { setClearing(false); }
  }
  async function retry(job: TaskInfo) {
    if (retries.current.has(job.id)) return;
    retries.current.add(job.id);setRetrying(current => [...current,job.id]);
    try {
      const result = await request<TaskInfo>(`/tasks/subtitles/${job.id}/retry`,new AbortController().signal,{});
      window.dispatchEvent(new CustomEvent('video-parser:restore-task',{detail:{kind:'subtitles',id:result.id}}));
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : '重试失败，请检查后再试。'); }
    finally { retries.current.delete(job.id);setRetrying(current => current.filter(id => id !== job.id)); }
  }
  return { jobs, error, loaded, reload, facets, setFacets, resetFilters, notice, filter, setFilter, kind, setKind, query, setQuery, statusCounts, kindCount, retrying,
    pendingCancels, visible, cancel, retry, deadCount: dead.length, clearing, clearDead };
}
