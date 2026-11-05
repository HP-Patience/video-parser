import { request } from '../../lib/api';

export const IMAGE_LIMIT = 4;
export const IMAGE_MAX_BYTES = 4 * 1024 * 1024;
const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

/** A pasted image already stored beside the project; only the name travels. */
export type StoredImage = { name: string; mime?: string; size?: number };

export const attachmentUrl = (projectId: string, name: string) =>
  `/api/workspace/projects/${projectId}/attachments/${name}`;

export function clipboardImages(event: { clipboardData: DataTransfer | null }): File[] {
  const items = event.clipboardData?.items;
  if (!items) return [];
  const files: File[] = [];
  for (const item of Array.from(items)) {
    if (item.kind !== 'file' || !ACCEPTED.includes(item.type)) continue;
    const file = item.getAsFile();
    if (file) files.push(file);
  }
  return files;
}

function dataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('无法读取这张图片，请重新粘贴。'));
    reader.readAsDataURL(file);
  });
}

/** Uploads immediately so a draft only ever holds a short file name. */
export async function uploadImage(projectId: string, file: File): Promise<StoredImage> {
  if (file.size > IMAGE_MAX_BYTES) throw new Error('单张图片不能超过 4 MB，请压缩后重试。');
  const data = await dataUrl(file);
  return request<StoredImage>(`/projects/${projectId}/attachments`, new AbortController().signal, { data });
}

/** Uploads what the clipboard carried, keeping the existing order and the cap. */
export async function uploadClipboard(projectId: string, files: File[], room: number): Promise<StoredImage[]> {
  const accepted = files.slice(0, Math.max(0, room));
  if (!accepted.length) throw new Error(`最多添加 ${IMAGE_LIMIT} 张图片。`);
  const stored: StoredImage[] = [];
  for (const file of accepted) stored.push(await uploadImage(projectId, file));
  return stored;
}
