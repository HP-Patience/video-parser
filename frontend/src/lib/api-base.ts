const STORAGE_KEY = 'video-parser:cloud-base-url';

function readStored(): string {
  try { return localStorage.getItem(STORAGE_KEY) || ''; } catch { return ''; }
}

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, '');
  if (!trimmed) return '';
  try {
    const url = new URL(trimmed);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return '';
    return url.toString().replace(/\/+$/, '');
  } catch { return ''; }
}

let baseUrl = normalizeBaseUrl(readStored());

export function getApiBaseUrl(): string { return baseUrl; }

export function setApiBaseUrl(value: string): boolean {
  const normalized = normalizeBaseUrl(value);
  if (value.trim() && !normalized) return false;
  baseUrl = normalized;
  try {
    if (normalized) localStorage.setItem(STORAGE_KEY, normalized);
    else localStorage.removeItem(STORAGE_KEY);
  } catch { /* A session-only value is still useful in restricted WebViews. */ }
  return true;
}

export function workspaceUrl(path: string): string {
  const suffix = '/api/workspace' + (path.startsWith('/') ? path : '/' + path);
  return baseUrl ? baseUrl + suffix : suffix;
}
