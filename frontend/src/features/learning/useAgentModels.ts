import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { AgentCatalog, AgentTarget, AISettings } from '../../lib/api';
import { isEnabled, targetKey, useAgentPreferences } from './agentPreferences';

const engines = ['api', 'claude', 'codex', 'opencode', 'dsh'];

export default function useAgentModels(api: AISettings | null, fallbackConfigured = false) {
  // The default target is chosen in Settings; a choice made here applies to this page until the default changes.
  const preferences = useAgentPreferences();
  const [selected, setSelected] = useState<AgentTarget>(preferences.target);
  const defaultKey = targetKey(preferences.target) + '|' + preferences.target.effort;
  const appliedDefault = useRef(defaultKey);
  useEffect(() => {
    if (appliedDefault.current === defaultKey) return;
    appliedDefault.current = defaultKey; setSelected(preferences.target);
  }, [defaultKey, preferences.target]);
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const probe = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    request<AgentCatalog>('/ai/agents', controller.signal).then(setCatalog).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取本地 agent。');
    });
    return () => { controller.abort(); probe.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const reload = () => { request<AgentCatalog>('/ai/agents', controller.signal).then(setCatalog).catch(() => {}); };
    window.addEventListener('video-parser:agents-changed', reload);
    return () => { controller.abort(); window.removeEventListener('video-parser:agents-changed', reload); };
  }, []);
  const select = useCallback((next: AgentTarget) => {
    if (!engines.includes(next.engine)) return;
    setSelected(old => old.engine === next.engine && old.model === next.model && old.effort === next.effort ? old : next);
    setError('');
  }, []);
  const refresh = useCallback(async () => {
    if (probe.current) return;
    const controller = new AbortController(); probe.current = controller;
    setRefreshing(true); setError('');
    try {
      const next = await request<AgentCatalog>('/ai/agents/discover', controller.signal, {
        confirm: true, engine: selected.engine === 'api' ? 'all' : selected.engine, model: selected.model,
      });
      if (controller.signal.aborted) return;
      setCatalog(next);
      const failed = next.engines.find(item => selected.engine === 'api' ? item.installed && !!item.error : item.id === selected.engine && !!item.error);
      if (failed) setError(failed.error);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取本地 agent。');
    } finally {
      if (probe.current === controller) { probe.current = null; setRefreshing(false); }
    }
  }, [selected.engine, selected.model]);
  return useMemo(() => {
    const native = selected.engine !== 'api';
    const provider = catalog?.engines.find(item => item.id === selected.engine);
    const model = provider?.models.find(item => item.id === selected.model);
    return { selected, select, catalog, error, refreshing, refresh, native, model,
      key: [selected.engine, selected.model, selected.effort].join('|'),
      configured: native ? !!provider?.installed && provider?.enabled !== false : api?.configured ?? fallbackConfigured,
      name: native ? provider?.name || selected.engine : api?.provider_name || 'API',
      apiModel: api?.model || '', commands: provider?.commands || [], efforts: model?.efforts || [],
      enabled: (target: Pick<AgentTarget, 'engine' | 'model'>) => (target.engine === 'api' || catalog?.engines.find(item => item.id === target.engine)?.enabled !== false) && isEnabled(preferences, target),
    };
  }, [selected, select, catalog, error, refreshing, refresh, preferences, api?.configured, api?.provider_name, api?.model, fallbackConfigured]);
}

export type AgentModelsState = ReturnType<typeof useAgentModels>;
