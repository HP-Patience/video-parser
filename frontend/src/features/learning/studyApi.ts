export interface PlaybackPoint { position: number; duration: number; revision: number }
export interface NoteImage { name: string; mime?: string; size?: number }
export interface PersonalNote { id: string; time: number; text: string; revision: number; images?: NoteImage[]; created_at?: string; updated_at?: string }
export interface NoteDraft extends PersonalNote { dirty: boolean }
export class StudyError extends Error { constructor(message: string, public status = 0) { super(message); } }
export async function studyRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch('/api/workspace' + path, { method: body === undefined ? 'GET' : 'POST', signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), keepalive: body !== undefined, cache: 'no-store' });
  } catch { throw new StudyError('无法连接工作台，未保存内容仍保留在本机草稿中。'); }
  const data = await response.json().catch(() => null);
  if (!response.ok || data === null) throw new StudyError(typeof data?.detail === 'string' ? data.detail : '学习数据保存失败，请重试。', response.status);
  return data as T;
}
export const studyPath = (project: string, kind: string) => `/projects/${project}/study/${kind}`;
export const backupKey = (project: string, resource: string, kind: string) => `video-parser:study:${project}:${encodeURIComponent(resource)}:${kind}`;
export function readBackup<T>(key: string): T | null { try { return JSON.parse(localStorage.getItem(key) || 'null') as T | null; } catch { return null; } }
export function writeBackup(key: string, value: unknown): boolean { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
export function clearBackup(key: string) { try { localStorage.removeItem(key); } catch { /* Server data is still durable. */ } }
export const noteId = () => crypto.randomUUID().replaceAll('-', '');
export const errorText = (error: unknown) => error instanceof Error ? error.message : '保存失败，请重试。';
