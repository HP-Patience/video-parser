import { request } from './api';

export interface TaskInfo {
  created?: number; details?: { template?: string; source?: string; engine?: string; model?: string; frame_mode?: string; count?: number };
  id: string; state: string; message: string; kind?: string; title?: string; operation?: string;
  phase?: string; progress?: { done?: number; total?: number; bytes?: number; total_bytes?: number; speed_bytes_per_second?: number; eta_seconds?: number; sampled_at?: number; track?: number; tracks?: number; estimated_total?: number };
  error_code?: string; queue_position?: number; elapsed_seconds?: number; project_id?: string;
  /** Current title and episode of the linked project (renames show up in older task records too). */
  project_title?: string | null; part?: number | null;
  file_names?: string[];
  /** Linked source media, separate from subtitle/translation outputs. */
  video_names?: string[];
  subjects?: { project_id: string | null; part: number | null; title: string }[];
  file_details?: { project_id: string | null; part: number | null; role: string; name: string }[];
}
export interface WorkflowJob<T> extends TaskInfo { result: T }
export const isActive = (state?: string) => ['queued', 'running', 'cancelling'].includes(state || '');
export const phases: Record<string, string> = { queued:'排队', cancelling:'正在取消', starting:'启动', reading:'读取信息', downloading:'下载', merging:'合并音视频', transcribing:'转录', translating:'AI 翻译', extracting:'提取画面', analyzing:'模型分析', validating:'校验', archiving:'归档', completed:'完成', empty:'未识别到语音', partial:'部分完成', failed:'失败', login_required:'需要登录', cancelled:'已取消', interrupted:'重启中断' };

function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
  });
}

export async function pollJob<T extends TaskInfo>(path: string, signal: AbortSignal, update: (job: T) => void): Promise<T> {
  let failures = 0;
  while (!signal.aborted) {
    let result: T;
    try { result = await request<T>(path, signal); failures = 0; }
    catch (error) {
      if (signal.aborted || ++failures >= 5) throw error;
      await delay(Math.min(1000 * 2 ** failures, 10000), signal); continue;
    }
    update(result);
    if (!isActive(result.state)) return result;
    await delay(document.hidden ? 5000 : 1000, signal);
  }
  throw new DOMException('Aborted', 'AbortError');
}

export function remember(kind: string, id: string) {
  try { localStorage.setItem('video-parser:task:' + kind, id); } catch { /* Optional browser convenience. */ }
}
export function remembered(kind: string): string | null {
  try { const id = localStorage.getItem('video-parser:task:' + kind); return id && /^[a-f0-9]{32}$/.test(id) ? id : null; } catch { return null; }
}
export async function runWorkflow<T>(path: string, body: unknown, signal: AbortSignal, update: (job: TaskInfo) => void): Promise<T> {
  const initial = await request<WorkflowJob<T>>(path, signal, body);
  update(initial);
  const job = isActive(initial.state) ? await pollJob<WorkflowJob<T>>('/tasks/' + initial.id, signal, update) : initial;
  if (job.state !== 'completed') {
    const message = job.message?.trim();
    const fallback = job.state === 'login_required' ? '平台要求登录，请到“账号与登录”完成登录后重试。'
      : job.state === 'cancelled' ? '任务已取消，可以重新开始。'
      : job.state === 'interrupted' ? '应用重启导致任务中断，请重新开始。'
      : job.state === 'partial' ? '任务只完成了一部分，请到任务中心查看并重试未完成部分。'
      : '任务没有完成，请检查网络、登录状态或本机工具后重试。';
    const readable = message && !/traceback|stack trace|request id|sqlite|sqlalchemy|typeerror|referenceerror|undefined|null/i.test(message) && /[\u3400-\u9fff]/.test(message)
      ? message.replace(/\s*\[(?:[A-Z][A-Z0-9_-]{1,}|[45]\d{2})\]\s*$/, '') : fallback;
    throw new Error(readable);
  }
  return job.result;
}
export function cancelTask(kind: string, id: string, signal = new AbortController().signal) {
  return request<TaskInfo>(`/tasks/${kind}/${id}/cancel`, signal, {});
}
