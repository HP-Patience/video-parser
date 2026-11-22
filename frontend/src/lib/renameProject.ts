import { request } from './api';
import type { VideoProject } from './api';
import { isActive, pollJob, type TaskInfo } from './jobs';

/**
 * Rename the archived files of one project.
 *
 * The same transactional pipeline as the 文件重命名 tab: the server builds a manual
 * plan, validates it (preview + token) and applies it, so inline editing cannot skip
 * the collision, length and Windows-name checks. The job id is polled here and the
 * refreshed project is returned, because paths, resources and episode title all change.
 */
export async function renameProjectFiles(identifier: string, title: string, signal: AbortSignal): Promise<VideoProject> {
  const started = await request<TaskInfo>(`/projects/${identifier}/rename`, signal, { title });
  const done = isActive(started.state) ? await pollJob<TaskInfo>(`/naming/tasks/${started.id}`, signal, () => {}) : started;
  if (done.state !== 'completed') throw new Error(done.message || '改名没有完成，原文件仍在。');
  return request<VideoProject>(`/projects/${identifier}`, signal);
}
