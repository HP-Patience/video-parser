import { useEffect, useState } from 'react';
import { request } from '../../lib/api';
import { isActive, type TaskInfo } from '../../lib/jobs';

export default function useTaskHistory(onActivityChange?: (count: number | null) => void) {
  const [jobs,setJobs] = useState<TaskInfo[]>([]);
  const [error,setError] = useState('');
  const [loaded,setLoaded] = useState(false);
  const [reload,setReload] = useState(0);
  useEffect(() => {
    setLoaded(false);
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try { const data = await request<{ jobs:TaskInfo[] }>('/tasks',controller.signal);if (!controller.signal.aborted) { setJobs(data.jobs);setLoaded(true);setError('');onActivityChange?.(data.jobs.filter(job => isActive(job.state)).length); } }
      catch (reason) { if (!controller.signal.aborted) { onActivityChange?.(null);setError(reason instanceof Error ? reason.message : '无法读取任务记录'); } }
      finally { if (!controller.signal.aborted) timer=setTimeout(() => { void load(); },document.hidden ? 8000 : 2000); }
    };
    void load(); return () => { controller.abort();clearTimeout(timer); };
  },[onActivityChange,reload]);
  return { jobs, error, loaded, reload: () => setReload(value => value + 1) };
}
