import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { request } from '../../lib/api';
import type { AgentCatalog, AgentTarget, AISettings } from '../../lib/api';
import { API_TARGET, isEnabled, saveAgentPreferences, targetKey, useAgentPreferences } from '../learning/agentPreferences';
import { Switch } from '../../components/ChoiceControls';
import './learning-targets.css';

type Row = { key: string; target: AgentTarget; label: string; ready: boolean };
const ENGINES = [{ id: 'claude', name: 'Claude Code' }, { id: 'codex', name: 'Codex' }, { id: 'opencode', name: 'OpenCode' }, { id: 'dsh', name: 'DeepSeek Harness' }] as const;

export default function LearningTargets({ api, children, focusRequest = 0 }: { api: AISettings | null; children: ReactNode; focusRequest?: number }) {
  const preferences = useAgentPreferences();
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [status, setStatus] = useState('');
  const [failed, setFailed] = useState(false);
  const [reading, setReading] = useState('');
  const [savingAgent, setSavingAgent] = useState('');
  const [opened, setOpened] = useState<string[]>([]);
  const [query, setQuery] = useState<Record<string, string>>({});
  const [showIds, setShowIds] = useState<Record<string, boolean>>({});
  const [discoveryErrors, setDiscoveryErrors] = useState<Record<string, string>>({});
  const discovery = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusRequest) { heading.current?.scrollIntoView({ block: 'start' }); heading.current?.focus({ preventScroll: true }); }
  }, [focusRequest]);
  useEffect(() => {
    const controller = new AbortController();
    request<AgentCatalog>('/ai/agents', controller.signal).then(setCatalog).catch((reason: unknown) => {
      if (!controller.signal.aborted) { setFailed(true); setStatus(reason instanceof Error ? reason.message : '无法读取本地 agent。'); }
    });
    return () => { controller.abort(); discovery.current?.abort(); };
  }, []);
  async function toggleAgent(engine: string, enabled: boolean) {
    const previous = catalog;
    setSavingAgent(engine); setStatus('');
    setDiscoveryErrors(old => ({ ...old, [engine]: '' }));
    setCatalog(old => old ? { ...old, engines: old.engines.map(item => item.id === engine ? { ...item, enabled } : item) } : old);
    try {
      const next = await request<AgentCatalog>('/ai/agents/enabled', new AbortController().signal, { engine, enabled });
      setCatalog(next); window.dispatchEvent(new CustomEvent('video-parser:agents-changed'));
      // 开关的轨道本身就是结果，不再额外弹一句「已开启 / 已关闭」；
      // 只清掉上一次的错误状态，失败提示仍走下面的 catch。
      setFailed(false);
    } catch (error) { setCatalog(previous); setFailed(true); setStatus(error instanceof Error ? error.message : '保存失败'); }
    finally { setSavingAgent(''); }
  }
  async function discover(engine: string) {
    const source = catalog?.engines.find(item => item.id === engine);
    if (discovery.current || !source?.installed || source.enabled === false) return;
    const controller = new AbortController(); discovery.current = controller;
    setDiscoveryErrors(old => ({ ...old, [engine]: '' }));
    setReading(engine); setStatus(''); setFailed(false);
    try {
      const next = await request<AgentCatalog>('/ai/agents/discover', controller.signal, { confirm: true, engine, model: '' });
      if (controller.signal.aborted) return;
      setCatalog(next);
      const problem = next.engines.find(item => item.id === engine && item.error);
      setDiscoveryErrors(old => ({ ...old, [engine]: problem?.error || '' }));
      setStatus(problem ? '' : '已读取本机模型列表。');
      window.dispatchEvent(new CustomEvent('video-parser:agents-changed'));
    } catch (reason) {
      if (!controller.signal.aborted) { setDiscoveryErrors(old => ({ ...old, [engine]: reason instanceof Error ? reason.message : '无法读取本地 agent。' })); }
    } finally { if (discovery.current === controller) { discovery.current = null; setReading(''); } }
  }
  const current = targetKey(preferences.target);
  const groups = ENGINES.map(source => {
    const engine = catalog?.engines.find(item => item.id === source.id);
    const ready = !!engine?.installed && engine.enabled !== false;
    const rows: Row[] = [{ key: source.id + '|', target: { engine: source.id, model: '', effort: '' }, label: '跟随工具默认模型', ready }];
    for (const model of engine?.models || []) rows.push({ key: targetKey({ engine: source.id, model: model.id }),
      target: { engine: source.id, model: model.id, effort: '' }, label: model.label, ready });
    if (preferences.target.engine === source.id && !rows.some(row => row.key === current)) {
      rows.push({ key: current, target: preferences.target, label: preferences.target.model || '跟随工具默认模型', ready });
    }
    return { ...source, engine, rows };
  });
  const apiRow: Row = { key: targetKey(API_TARGET), target: API_TARGET, label: api?.configured ? 'API · ' + api.provider_name + ' · ' + api.model : 'API（模型设置）', ready: true };
  const apiGroups = (api?.providers || []).map(provider => ({ id: provider.id, name: provider.name, rows: provider.models.map(model => {
    const target: AgentTarget = { engine: 'api', provider_id: provider.id, model, effort: '' };
    return { key: targetKey(target), target, label: model, ready: provider.configured };
  }) }));
  const showLegacyAPI = !api?.providers?.length || (preferences.target.engine === 'api' && !preferences.target.provider_id);
  const known = [apiRow, ...apiGroups.flatMap(group => group.rows), ...groups.flatMap(group => group.rows)];
  const visibleAPIGroups = apiGroups.map(group => ({ ...group, rows: group.rows.filter(row => row.ready) })).filter(group => group.rows.length > 0);
  const visibleGroups = groups.map(group => ({ ...group, rows: group.rows.filter(row => row.ready && isEnabled(preferences, row.target)) }))
    .filter(group => group.rows.length > 0);
  const offered = [...(showLegacyAPI ? [apiRow] : []), ...visibleAPIGroups.flatMap(group => group.rows), ...visibleGroups.flatMap(group => group.rows)];
  const selected = known.find(row => row.key === current);
  const currentVisible = offered.some(row => row.key === current);
  const enabledKeys = () => preferences.enabled ?? known.map(row => row.key);
  function store(enabled: string[], target: AgentTarget, message: string) {
    const saved = saveAgentPreferences({ enabled, target });
    setFailed(!saved); setStatus(saved ? message : '当前页已生效，关闭或刷新后可能丢失。');
  }
  function toggle(row: Row, on: boolean) {
    if (row.key === current) return;
    const keys = enabledKeys().filter(key => key !== row.key);
    if (on) keys.push(row.key);
    store(keys, preferences.target, on ? '已在模型选择器中显示。' : '已在模型选择器中隐藏。');
  }
  function makeDefault(key: string) {
    const row = offered.find(item => item.key === key);
    if (!row?.ready) return;
    const keys = enabledKeys();
    store(keys.includes(row.key) ? keys : [...keys, row.key], row.target, '默认模型已保存，仅此浏览器生效。');
  }
  const problem = !selected ? '所选默认模型不在当前列表，请检查工具或更换。' : selected.target.engine === 'api'
    ? api && (selected.target.provider_id ? !selected.ready : !api.configured) ? '所选供应商尚未配置完成，请检查接口地址、密钥和模型。' : ''
    : !catalog ? '正在读取所选工具状态…' : selected.ready ? '' : '所选默认工具未安装或已关闭，请检查对应来源；不会自动切换模型。';

  return <div className="learning-targets">
    <section className="learning-default-control settings-card" aria-labelledby="learning-targets-title">
      <div className="learning-targets-title"><label id="learning-targets-title" htmlFor="learning-default-model">默认模型</label></div>
      <select id="learning-default-model" aria-describedby={problem ? 'learning-default-state' : undefined} value={currentVisible ? current : ''} onChange={event => makeDefault(event.target.value)}>
        {!currentVisible && <option value="" disabled>请选择默认模型</option>}
        {showLegacyAPI && <option value={apiRow.key} translate={api?.configured ? 'no' : undefined}>{apiRow.label}</option>}
        {visibleAPIGroups.map(group => <optgroup key={group.id} label={'API · ' + group.name}>{group.rows.map(row =>
          <option key={row.key} value={row.key} translate="no">{group.name + ' · ' + row.label}</option>)}</optgroup>)}
        {visibleGroups.map(group => <optgroup key={group.id} label={group.name}>{group.rows.map(row =>
          <option key={row.key} value={row.key} translate={row.target.model ? 'no' : undefined}>{group.name + ' · ' + row.label}</option>)}</optgroup>)}
      </select>
      {problem && <p id="learning-default-state" className="settings-hint" role="status">{problem}</p>}
    </section>
    {status && <p role={failed ? 'alert' : 'status'} aria-live="polite" className={failed ? 'error-text' : 'settings-save-status'}>{status}</p>}
    {children}
    <section className="model-sources" aria-labelledby="model-sources-title">
      <h3 id="model-sources-title" ref={heading} tabIndex={-1}>本机 Agent 工具</h3>
      {groups.map(group => {
        const { engine } = group, open = opened.includes(group.id), term = (query[group.id] || '').trim().toLocaleLowerCase();
        const rows = group.rows.filter(row => !term || `${row.label} ${row.target.model}`.toLocaleLowerCase().includes(term));
        const state = !engine ? catalog || failed ? '状态未知' : '正在读取…' : engine.enabled === false ? '已关闭' : !engine.installed ? '未安装' : engine.discovered ? `${engine.models.length} 个模型` : '已安装 · 未读取模型';
        return <section key={group.id} className="model-source" aria-labelledby={'source-' + group.id}>
          <div className="model-source-row">
            <div className="model-source-copy"><h4 id={'source-' + group.id} translate="no">{group.name}</h4><span className="model-source-state">{state}</span></div>
            <div className="model-source-actions">
              <Switch className="settings-check agent-tool-switch" ariaLabel={group.name} label="允许调用"
                checked={!!engine && engine.enabled !== false} disabled={!engine || !!savingAgent || !!reading}
                onChange={next => { void toggleAgent(group.id, next); }} />
              <button type="button" aria-label={'管理 ' + group.name + ' 模型'} aria-expanded={open} aria-controls={'models-' + group.id}
                onClick={() => setOpened(old => open ? old.filter(id => id !== group.id) : [...old, group.id])}>{open ? '收起' : '管理模型'}</button>
            </div>
          </div>
          {engine?.enabled !== false && discoveryErrors[group.id] && <p className="error-text" role="alert">{discoveryErrors[group.id]}</p>}
          <div id={'models-' + group.id} className="model-source-config" hidden={!open}>
            <div className="learning-targets-heading">
              <label className="learning-model-search"><span className="sr-only">搜索可见模型</span><input type="search" aria-label={'搜索 ' + group.name + ' 模型'} placeholder="搜索模型名称或 ID" value={query[group.id] || ''}
                onChange={event => setQuery(old => ({ ...old, [group.id]: event.target.value }))} onKeyDown={event => { if (event.key === 'Escape') setQuery(old => ({ ...old, [group.id]: '' })); }} /></label>
              <button type="button" disabled={!!reading || !!savingAgent || !engine?.installed || engine.enabled === false} aria-busy={reading === group.id}
                title="只运行本机程序读取模型与命令，不发送课程内容" onClick={() => { void discover(group.id); }}>{reading === group.id ? '读取中…' : '读取本机模型'}</button>
            </div>
            <div className="learning-model-meta-tools"><Switch className="settings-check" label="显示调用 ID"
              checked={!!showIds[group.id]} onChange={next => setShowIds(old => ({ ...old, [group.id]: next }))} /></div>
            <fieldset className="learning-target-group" aria-label={group.name}>
              <ul>{rows.map(row => <li key={row.key} className={row.key === current ? 'is-default' : undefined}>
                <label className="settings-check"><input type="checkbox" checked={row.key === current || isEnabled(preferences, row.target)} disabled={row.key === current}
                  onChange={event => toggle(row, event.target.checked)} /><span className="learning-model-copy"><span translate={row.target.model ? 'no' : undefined}>{row.label}</span>
                  {showIds[group.id] && row.target.model && <code translate="no">{row.target.model}</code>}</span></label>
                {row.key === current && <small className="learning-model-default-badge">当前默认</small>}
              </li>)}</ul>
              {!rows.length && <p className="settings-hint" role="status">没有匹配的模型。</p>}
            </fieldset>
          </div>
        </section>;
      })}
    </section>
  </div>;
}
