import { useSyncExternalStore } from 'react';
import type { AgentTarget } from '../../lib/api';

// Browser-only learning preferences: which engines/models the learning page offers and which one it opens with.
// Never holds credentials and never changes an agent's own (global) configuration.
export const AGENT_PREFERENCES_KEY = 'video-parser:agent-preferences';
const LEGACY_TARGET_KEY = 'video-parser:agent-target';
const ENGINES = ['api', 'claude', 'codex', 'opencode', 'dsh'];
const MODEL = /^[A-Za-z0-9][A-Za-z0-9_.:/@+\[\]-]{0,199}$/;
const EFFORT = /^(?:[a-z][a-z0-9_-]{0,31})?$/;

/** enabled === null: the user has not curated a list yet, so every installed engine/model stays available. */
export type AgentPreferences = { enabled: string[] | null; target: AgentTarget };
export const API_TARGET: AgentTarget = { engine: 'api', model: '', effort: '' };
export const targetKey = (target: Pick<AgentTarget, 'engine' | 'model' | 'provider_id'>) => target.engine === 'api' && target.provider_id
  ? 'api|' + encodeURIComponent(target.provider_id) + '|' + encodeURIComponent(target.model)
  : target.engine + '|' + encodeURIComponent(target.model);

export function targetFromKey(key: string): AgentTarget | null {
  try {
    const parts = key.split('|');
    if (parts[0] === 'api' && parts.length === 3) return validTarget({ engine: 'api', provider_id: decodeURIComponent(parts[1]), model: decodeURIComponent(parts[2]), effort: '' });
    if (parts.length === 2) return validTarget({ engine: parts[0], model: decodeURIComponent(parts[1]), effort: '' });
  } catch { /* A stored or imported key can be malformed. */ }
  return null;
}

export function validTarget(value: unknown): AgentTarget | null {
  if (!value || typeof value !== 'object') return null;
  const { engine, model, effort, provider_id } = value as Record<string, unknown>;
  if (typeof engine !== 'string' || !ENGINES.includes(engine) || typeof model !== 'string' || typeof effort !== 'string') return null;
  const modelValid = engine === 'api' && provider_id !== undefined
    ? model.length <= 200 && !/[\s\u0000-\u001f\u007f]/u.test(model) : !model || MODEL.test(model);
  if (!modelValid || !EFFORT.test(effort)) return null;
  if (provider_id !== undefined && (engine !== 'api' || typeof provider_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(provider_id))) return null;
  if (engine === 'api' && (effort || (provider_id ? !model : !!model))) return null;
  return { engine: engine as AgentTarget['engine'], model, effort, ...(typeof provider_id === 'string' ? { provider_id } : {}) };
}

export function parseAgentPreferences(stored: string | null, legacy: string | null = null): AgentPreferences {
  try {
    const data: unknown = JSON.parse(stored || 'null');
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      const record = data as Record<string, unknown>;
      const enabled = Array.isArray(record.enabled)
        ? [...new Set(record.enabled.filter((key): key is string => typeof key === 'string' && key.length <= 640))].slice(0, 400) : null;
      return { enabled, target: validTarget(record.target) || API_TARGET };
    }
    // First run after the upgrade: the last chat choice becomes the default, nothing else changes.
    return { enabled: null, target: validTarget(JSON.parse(legacy || 'null')) || API_TARGET };
  } catch { return { enabled: null, target: API_TARGET }; }
}

export function isEnabled(preferences: AgentPreferences, target: Pick<AgentTarget, 'engine' | 'model'>) {
  return target.engine === 'api' || preferences.enabled === null || preferences.enabled.includes(targetKey(target));
}

function read(): AgentPreferences {
  try { return parseAgentPreferences(localStorage.getItem(AGENT_PREFERENCES_KEY), localStorage.getItem(LEGACY_TARGET_KEY)); }
  catch { return { enabled: null, target: API_TARGET }; }
}

let current: AgentPreferences | null = null;
const listeners = new Set<() => void>();
const snapshot = () => (current ??= read());
function subscribe(listener: () => void) {
  listeners.add(listener);
  const storage = (event: StorageEvent) => { if (event.key === AGENT_PREFERENCES_KEY) { current = read(); listener(); } };
  window.addEventListener('storage', storage);
  return () => { listeners.delete(listener); window.removeEventListener('storage', storage); };
}

/** Applies at once in this page; returns false when the browser refused to persist it. */
export function saveAgentPreferences(next: AgentPreferences): boolean {
  current = { enabled: next.enabled ? [...new Set(next.enabled)] : null, target: validTarget(next.target) || API_TARGET };
  listeners.forEach(listener => listener());
  try { localStorage.setItem(AGENT_PREFERENCES_KEY, JSON.stringify(current)); return true; } catch { return false; }
}

export const useAgentPreferences = () => useSyncExternalStore(subscribe, snapshot);
