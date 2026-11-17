import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { AgentCatalog, AgentTarget, AISettings } from '../../lib/api';
import { isEnabled, targetKey, useAgentPreferences } from './agentPreferences';

const engines = ['api', 'claude', 'codex', 'opencode', 'dsh'];

export default function useAgentModels(settings: AISettings | null, fallbackConfigured = false) {
  const [loadedAPI, setLoadedAPI] = useState<AISettings | null>(null);
  const api = settings ?? loadedAPI;
  useEffect(() => {
    if (settings) return;
    const controller = new AbortController();
    request<AISettings>('/ai/settings', controller.signal).then(setLoadedAPI).catch(() => {});
    return () => controller.abort();
  }, [settings]);
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
    setSelected(old => targetKey(old) === targetKey(next) && old.effort === next.effort ? old : next);
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
    const supplier = selected.provider_id ? api?.providers?.find(item => item.id === selected.provider_id) : undefined;
    const apiSettings: AISettings | null = native || !api ? null : selected.provider_id
      ? supplier ? { ...api, ...supplier, provider_name: supplier.name, model: selected.model } : null : api;
    return { selected, select, catalog, error, refreshing, refresh, native, model, apiSettings,
      key: targetKey(selected) + '|' + selected.effort,
      configured: native ? !!provider?.installed && provider?.enabled !== false
        : selected.provider_id ? !!supplier?.configured && supplier.models.includes(selected.model) : api?.configured ?? fallbackConfigured,
      name: native ? provider?.name || selected.engine : apiSettings?.provider_name || 'API',
      apiModel: apiSettings?.model || '', apiProviders: api?.providers || [], commands: provider?.commands || [], efforts: model?.efforts || [],
      enabled: (target: Pick<AgentTarget, 'engine' | 'model'>) => (target.engine === 'api' || catalog?.engines.find(item => item.id === target.engine)?.enabled !== false) && isEnabled(preferences, target),
    };
  }, [selected, select, catalog, error, refreshing, refresh, preferences, api, fallbackConfigured]);
}

export type AgentModelsState = ReturnType<typeof useAgentModels>;
