import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { AIProvider, AISettings, AIRuntimeStatus, AITestTarget, AITestResult, AITestResponse } from '../../lib/api';
import LocalAIStartup, { launchError, launchPayload, toLaunchDraft } from './LocalAIStartup';
import type { LaunchDraft } from './LocalAIStartup';
import LocalAIRuntime from './LocalAIRuntime';

type Draft = { id?: string; name: string; provider: string; base_url: string; model: string; models: string[];
  api_key: string; clear_key: boolean; price_input: string; price_output: string };
type TestOption = { label: string; target: AITestTarget; disabled?: boolean };
const uniqueModels = (models: string[]) => [...new Set(models.map(model => model.trim()).filter(Boolean))];
const serviceURL = (value: string) => value.trim().replace(/\/+$/, '');
const localURL = (value: string) => {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname); }
  catch { return false; }
};
const priceText = (value: number | null) => value === null ? '' : String(value);
const toDraft = (value: AIProvider): Draft => ({ id: value.id, name: value.name, provider: value.provider, base_url: value.base_url,
  model: value.model, models: uniqueModels(value.models), api_key: '', clear_key: false,
  price_input: priceText(value.price_input), price_output: priceText(value.price_output) });
const legacyProvider = (value: AISettings): AIProvider => ({ ...value, id: 'legacy', name: value.provider_name, models: value.model ? [value.model] : [] });
const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

export default function AISettingsSection({ onChange, onLoad, autoFocus = false, visible = true }: {
  onChange: (settings: AISettings) => void; onLoad?: (settings: AISettings) => void; autoFocus?: boolean; visible?: boolean;
}) {
  const [saved, setSaved] = useState<AISettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [launchDraft, setLaunchDraft] = useState<LaunchDraft | null>(null);
  const [manualModel, setManualModel] = useState('');
  const [discovered, setDiscovered] = useState<string[]>([]);
  const [discoverySelection, setDiscoverySelection] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [failed, setFailed] = useState(false);
  const [working, setWorking] = useState<'save' | 'batch-test' | 'start' | 'pick' | 'discover' | 'delete' | null>(null);
  const [testResults, setTestResults] = useState<(AITestResult & { label: string })[]>([]);
  const controller = useRef<AbortController | null>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const testIds = useRef(new Map<string, string>());
  function targetId(parts: string[]) {
    const key = JSON.stringify(parts);
    let id = testIds.current.get(key);
    if (!id) { id = `target-${testIds.current.size}`; testIds.current.set(key, id); }
    return id;
  }
  useEffect(() => {
    const current = new AbortController(); controller.current = current;
    request<AISettings>('/ai/settings', current.signal).then(value => {
      if (current.signal.aborted) return;
      setSaved(value); onLoad?.(value);
      if (value.startup_error) { setFailed(true); setStatus(value.startup_error); }
    }).catch(reason => { if (!current.signal.aborted) { setFailed(true); setStatus(message(reason, '无法读取 AI 设置。')); } });
    return () => current.abort();
  }, []);
  const providers = saved ? (saved.providers ?? [legacyProvider(saved)]) : [];
  const legacy = !!saved && !saved.providers;
  const editing = draft?.id ? providers.find(item => item.id === draft.id) : undefined;
  function editProvider(item?: AIProvider) {
    if (!saved) return;
    const template = saved.presets.custom ? 'custom' : Object.keys(saved.presets)[0] || 'custom';
    setDraft(item ? toDraft(item) : { name: '', provider: template, base_url: '',
      model: '', models: [], api_key: '', clear_key: false, price_input: '', price_output: '' });
    const startup = item?.startup ?? saved.startup_defaults;
    setLaunchDraft(startup ? toLaunchDraft(startup) : null);
    setManualModel(''); setDiscovered([]); setDiscoverySelection([]); setSearch(''); setDeleteId(null); setStatus(''); setFailed(false);
    setTestResults([]);
  }
  useEffect(() => {
    if (autoFocus && saved) editProvider(providers[0]);
  }, [autoFocus, !!saved]);
  useEffect(() => {
    if (!draft) return;
    const frame = requestAnimationFrame(() => nameInput.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [draft?.id, !!draft]);
  if (!saved) return <section className="settings-ai model-source" aria-labelledby="settings-ai"><h3 id="settings-ai">模型供应商</h3>
    <p role={failed ? 'alert' : 'status'} className={failed ? 'error-text' : 'settings-hint'}>{status || '正在读取…'}</p></section>;

  const busy = working !== null;
  const sameService = !!editing && !!draft && draft.provider === editing.provider && serviceURL(draft.base_url) === serviceURL(editing.base_url);
  const priceInvalid = !!draft && [draft.price_input, draft.price_output].some(value => value !== '' && (!Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 10000));
  let urlInvalid = false;
  if (draft) { try { const url = new URL(draft.base_url); urlInvalid = !['http:', 'https:'].includes(url.protocol) || !!url.username || !!url.password; } catch { urlInvalid = true; } }
  const launchInvalid = draft?.provider === 'llamacpp' && (!launchDraft || !!launchError(launchDraft));
  const invalid = !draft || !draft.name.trim() || urlInvalid || !draft.models.length || !draft.models.includes(draft.model) || priceInvalid || launchInvalid;
  const dirty = !!draft && (!editing || JSON.stringify(toDraft(editing)) !== JSON.stringify(draft)
    || (draft.provider === 'llamacpp' && !!launchDraft && JSON.stringify(launchPayload(launchDraft)) !== JSON.stringify(editing.startup)));
  const update = (patch: Partial<Draft>) => { if (draft) setDraft({ ...draft, ...patch }); setStatus(''); setFailed(false); setTestResults([]); };
  function addModels(values: string[]) {
    if (!draft) return;
    const models = uniqueModels([...draft.models, ...values]);
    update({ models, model: draft.model || models[0] || '' });
    setManualModel('');
  }
  function chooseTemplate(provider: string) {
    const next = saved!.presets[provider];
    update({ provider, api_key: '', clear_key: false, ...(provider === 'custom' ? {} : { base_url: next?.base_url || '' }) });
    setDiscovered([]); setDiscoverySelection([]);
  }
  const canUseSavedKey = sameService && !draft?.clear_key && !!editing?.key_saved;
  const testOptions: TestOption[] = draft ? draft.models.map(model => ({
    label: dirty ? `${draft.name.trim() || '新供应商'}（未保存）` : draft.name,
    target: { id: targetId(['draft', draft.id || '', draft.provider, serviceURL(draft.base_url), model]),
      provider: draft.provider, base_url: draft.base_url.trim(), model,
      ...(sameService && !draft.clear_key && !legacy ? { provider_id: draft.id } : {}),
      ...(!(legacy && canUseSavedKey && !draft.api_key.trim()) ? { api_key: draft.api_key.trim() } : {}) },
    disabled: urlInvalid || (!localURL(draft.base_url) && !draft.api_key.trim() && !canUseSavedKey),
  })) : [];
  const reportError = (reason: unknown, fallback: string) => { if (!controller.current?.signal.aborted) { setFailed(true); setStatus(message(reason, fallback)); } };
  function apply(value: AISettings) { setSaved(value); onChange(value); }
  async function save(activate = false): Promise<AIProvider | null> {
    if (!draft || invalid || !controller.current) return null;
    if (legacy && draft.models.length > 1) { setFailed(true); setStatus('后端版本较旧，请升级后再保存多个模型。'); return null; }
    setWorking('save'); setStatus(''); setFailed(false);
    try {
      const body = { name: draft.name.trim(), provider: draft.provider, base_url: draft.base_url.trim(), models: uniqueModels(draft.models), model: draft.model,
        api_key: draft.api_key.trim() || null, clear_key: draft.clear_key,
        price_input: draft.price_input === '' ? null : Number(draft.price_input), price_output: draft.price_output === '' ? null : Number(draft.price_output),
        ...(draft.provider === 'llamacpp' && launchDraft ? { startup: launchPayload(launchDraft) } : {}),
        ...(!legacy && draft.id ? { id: draft.id } : {}), ...(!legacy ? { activate } : {}) };
      const value = await request<AISettings>(legacy ? '/ai/settings' : '/ai/providers', controller.current.signal, body);
      if (controller.current.signal.aborted) return null;
      const item = value.providers ? value.providers.find(item => item.id === draft.id)
        || value.providers.find(item => !providers.some(previous => previous.id === item.id)) : legacyProvider(value);
      apply(value);
      if (item) { setDraft(toDraft(item)); setLaunchDraft(item.startup ? toLaunchDraft(item.startup) : value.startup_defaults ? toLaunchDraft(value.startup_defaults) : null); }
      setDiscovered([]); setDiscoverySelection([]); setTestResults([]);
      setStatus(activate ? '已设为默认 API，供命令行启动和旧 API 调用使用。' : item?.configured ? '供应商已保存。' : '供应商已保存；尚未配置密钥，暂时不能分析。');
      return item || null;
    } catch (reason) { reportError(reason, '供应商保存失败。'); return null; }
    finally { if (!controller.current?.signal.aborted) setWorking(null); }
  }
  async function discoverModels() {
    if (!draft || urlInvalid || !controller.current) return;
    setWorking('discover'); setStatus('正在获取模型列表…'); setFailed(false); setDiscovered([]); setDiscoverySelection([]);
    try {
      const response = await request<{ models: string[] }>('/ai/providers/models', controller.current.signal, {
        ...(sameService && !draft.clear_key && draft.id ? { id: draft.id } : {}), provider: draft.provider, base_url: draft.base_url.trim(), api_key: draft.api_key.trim() || null,
      });
      if (controller.current.signal.aborted) return;
      const models = uniqueModels(response.models); setDiscovered(models); setSearch('');
      setStatus(models.length ? `已获取 ${models.length} 个模型，请勾选需要添加的模型。` : '服务未返回模型，可手动填写模型 ID。');
    } catch (reason) { reportError(reason, '无法获取模型列表，可手动添加模型。'); }
    finally { if (!controller.current?.signal.aborted) setWorking(null); }
  }
  async function removeProvider(id: string) {
    if (!controller.current) return;
    setWorking('delete'); setStatus(''); setFailed(false);
    try {
      const value = await request<AISettings>(`/ai/providers/${encodeURIComponent(id)}/delete`, controller.current.signal, {});
      if (controller.current.signal.aborted) return;
      apply(value); setDeleteId(null); if (draft?.id === id) setDraft(null);
      setTestResults([]); setStatus('供应商已删除。');
    } catch (reason) { reportError(reason, '供应商删除失败。'); }
    finally { if (!controller.current?.signal.aborted) setWorking(null); }
  }
  async function runTests(options: TestOption[]) {
    if (!controller.current || !options.length || options.length > 20 || options.some(option => option.disabled)) return;
    setWorking('batch-test'); setTestResults([]); setStatus(`正在测试 ${options.length} 个模型…`); setFailed(false);
    try {
      const response = await request<AITestResponse>('/ai/settings/test', controller.current.signal, { targets: options.map(option => option.target) });
      if (controller.current.signal.aborted) return;
      const results = options.map(option => ({ ...(response.results.find(item => item.id === option.target.id)
        || { id: option.target.id, provider: option.target.provider, model: option.target.model, ok: false, latency_ms: 0, error: '未收到这个目标的测试结果，请重试。' }), label: option.label }));
      setTestResults(results); const failures = results.filter(result => !result.ok).length;
      setFailed(failures > 0); setStatus(`模型测试完成：${results.length - failures} 项成功，${failures} 项失败。`);
    } catch (reason) { reportError(reason, '模型测试失败。'); }
    finally { if (!controller.current?.signal.aborted) setWorking(null); }
  }
  async function startModel(): Promise<AIRuntimeStatus | null> {
    const item = dirty ? await save() : editing;
    if (!item || !controller.current) return null;
    setWorking('start'); setStatus('正在提交模型启动请求…'); setFailed(false);
    try {
      const result = await request<AIRuntimeStatus>('/ai/runtime/start', controller.current.signal, { confirm: true, ...(!legacy ? { provider_id: item.id } : {}) });
      if (controller.current.signal.aborted) return null;
      setStatus(result.message); return result;
    } catch (reason) { reportError(reason, '启动请求未确认，请刷新运行状态后检查。'); return null; }
    finally { if (!controller.current?.signal.aborted) setWorking(null); }
  }
  const testAllOptions = testOptions.filter(option => !option.disabled).slice(0, 20);
  const testOptionFor = (model: string) => testOptions.find(option => option.target.model === model);
  const modelTestResults = testResults.length > 0 && <div className="ai-test-results" role="status" aria-live="polite" aria-label="模型测试结果"><ul>{testResults.map(result => <li key={result.id}>
    <span className="learning-model-copy"><span>{result.label}</span><code translate="no">{result.model}</code></span><span className="ai-test-outcome"><strong>{result.ok ? '成功' : '失败'}</strong><span>延迟 {result.latency_ms} 毫秒</span></span>
    {!result.ok && <p className="ai-test-error">{result.error || '连接失败，请检查模型和接口配置。'}</p>}</li>)}</ul></div>;
  const runModelTest = (model: string) => {
    const option = testOptionFor(model);
    if (option && !option.disabled) void runTests([option]);
  };
  const filteredModels = discovered.filter(model => model.toLowerCase().includes(search.toLowerCase()));
  return <section aria-labelledby="settings-ai" className="settings-ai ai-providers model-source">
    <div className="model-source-row ai-provider-heading"><div className="model-source-copy"><h3 id="settings-ai">模型供应商</h3><span className="model-source-state">{providers.length} 家供应商</span></div>
      {!draft && <button type="button" disabled={busy || legacy} onClick={() => editProvider()}>新增供应商</button>}</div>
    {legacy && <p className="settings-hint">后端版本较旧，当前显示原有单服务配置。升级后可新增供应商、获取模型列表并保存多个模型。</p>}
    {status && <p role={failed ? 'alert' : 'status'} aria-live="polite" className={failed ? 'error-text' : 'settings-save-status'}>{status}</p>}
    <ul className="ai-provider-list">{providers.map(item => <li className="ai-provider-card" key={item.id}>
      <div className="model-source-row"><div className="learning-model-copy"><h4 translate="no">{item.name}</h4><code translate="no">{item.base_url || '未填写接口地址'}</code>
        <span className="model-source-state">{item.models.length} 个模型 · {item.configured ? '已配置' : '未配置'}{(saved.active_provider_id === item.id || legacy) && ' · 默认 API'}</span></div>
        <div className="model-source-actions"><button type="button" aria-label={`编辑供应商 ${item.name}`} disabled={busy} aria-expanded={draft?.id === item.id} aria-controls="ai-provider-editor" onClick={() => editProvider(item)}>编辑</button>
          {!legacy && <button type="button" aria-label={`删除供应商 ${item.name}`} disabled={busy} onClick={() => setDeleteId(item.id)}>删除</button>}</div></div>
      {deleteId === item.id && <div className="ai-provider-delete" role="group" aria-label={`确认删除 ${item.name}`}><p>删除供应商及其保存的模型和密钥？</p>
        {saved.active_provider_id === item.id && <p className="error-text">这是当前默认 API。删除后默认模型将不可用，不会自动选择其他供应商。</p>}
        <div className="settings-actions"><button type="button" disabled={busy} onClick={() => { void removeProvider(item.id); }}>确认删除</button><button type="button" disabled={busy} onClick={() => setDeleteId(null)}>取消</button></div></div>}
    </li>)}</ul>
    {!providers.length && !draft && <p className="settings-hint">尚未添加供应商，请先添加接口和模型。</p>}
    {draft && <div key={draft.id || 'new'} id="ai-provider-editor" className="model-source-config ai-provider-editor" aria-labelledby="ai-provider-editor-title">
      <div className="ai-provider-editor-heading"><h4 id="ai-provider-editor-title">{editing ? '编辑供应商' : '新增供应商'}</h4>
        <button type="button" disabled={busy} onClick={() => setDraft(null)}>关闭编辑</button></div>
      <fieldset className="ai-provider-fields" disabled={busy}><legend className="sr-only">供应商配置</legend>
      <div className="ai-provider-layout">
      <fieldset className="ai-provider-connection" aria-label="连接信息"><div className="ai-common-fields">
        <div><label htmlFor="ai-provider-name">供应商名称</label><input ref={nameInput} id="ai-provider-name" value={draft.name} maxLength={80} onChange={event => update({ name: event.target.value })} /></div>
        <div><label htmlFor="ai-provider">服务模板</label><select id="ai-provider" value={draft.provider} onChange={event => chooseTemplate(event.target.value)}>
          {Object.entries(saved.presets).map(([key, item]) => <option key={key} value={key}>{item.name}</option>)}
          {!saved.presets[draft.provider] && <option value={draft.provider}>{draft.provider}</option>}</select></div>
        <div className="ai-field-wide"><label htmlFor="ai-base-url">接口地址</label><input id="ai-base-url" type="url" value={draft.base_url} spellCheck={false} placeholder="https://…/v1" onChange={event => { update({ base_url: event.target.value, api_key: '' }); setDiscovered([]); setDiscoverySelection([]); }} /></div>
        {draft.provider !== 'llamacpp' && <div className="ai-field-wide"><label htmlFor="ai-key">API Key</label><input id="ai-key" name="new-provider-api-key" type="password" autoComplete="new-password" spellCheck={false} value={draft.api_key}
          placeholder={sameService && editing?.key_saved && !draft.clear_key ? `已保存（${editing.key_hint}），留空表示不修改` : '粘贴 API Key；本机或无密钥服务可留空'} onChange={event => update({ api_key: event.target.value, clear_key: false })} />
          {sameService && editing?.key_saved && <label className="settings-check"><input type="checkbox" checked={draft.clear_key} onChange={event => update({ clear_key: event.target.checked, api_key: '' })} />保存时清除该供应商密钥</label>}</div>}
      </div>
      {!localURL(draft.base_url) && <div className="settings-price ai-provider-prices">
        <label>输入单价<input type="number" min="0" max="10000" step="0.01" inputMode="decimal" value={draft.price_input} placeholder="选填" onChange={event => update({ price_input: event.target.value })} /></label>
        <label>输出单价<input type="number" min="0" max="10000" step="0.01" inputMode="decimal" value={draft.price_output} placeholder="选填" onChange={event => update({ price_output: event.target.value })} /></label><span>元 / 百万 tokens，用于估算费用</span></div>}
      {urlInvalid && draft.base_url && <p className="error-text">请输入有效的 HTTP 或 HTTPS 接口地址，不要在地址中填写密钥。</p>}
      </fieldset>
      <fieldset className="ai-provider-models" aria-label="已添加模型">
        <label htmlFor="ai-model">手动添加模型 ID</label><div className="ai-provider-model-add"><input id="ai-model" value={manualModel} spellCheck={false} placeholder="输入模型 ID" onChange={event => setManualModel(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); if (!legacy || !draft.models.length) addModels([manualModel]); } }} /><button type="button" disabled={!manualModel.trim() || (legacy && draft.models.length > 0)} onClick={() => addModels([manualModel])}>添加模型</button>
          <button type="button" disabled={legacy || urlInvalid || !draft.base_url.trim() || (draft.clear_key && !draft.api_key.trim() && !localURL(draft.base_url))} aria-busy={working === 'discover'} onClick={() => { void discoverModels(); }}>{working === 'discover' ? '正在获取…' : '获取模型列表'}</button><button type="button" disabled={busy || !testAllOptions.length || testAllOptions.length > 20} aria-busy={working === 'batch-test'} onClick={() => { void runTests(testAllOptions); }}>{working === 'batch-test' ? '正在测试…' : '测试全部'}</button></div>
        <ul>{draft.models.map(model => { const testOption = testOptionFor(model); return <li key={model}><label className="settings-check"><input type="radio" name="ai-preferred-model" checked={draft.model === model} onChange={() => update({ model })} /><code translate="no">{model}</code><span>{draft.model === model ? '首选' : '设为首选'}</span></label>
          <div className="ai-provider-model-actions"><button type="button" disabled={busy || !testOption || !!testOption.disabled} aria-busy={working === 'batch-test'} aria-label={`测试模型 ${model}`} onClick={() => runModelTest(model)}>测试</button><button type="button" aria-label={`移除模型 ${model}`} onClick={() => { const models = draft.models.filter(item => item !== model); update({ models, model: draft.model === model ? models[0] || '' : draft.model }); }}>移除</button></div></li>; })}</ul>
      {discovered.length > 0 && <fieldset className="ai-provider-discovery"><label htmlFor="ai-discovery-search">搜索模型</label><input id="ai-discovery-search" type="search" value={search} onChange={event => setSearch(event.target.value)} />
        <ul>{filteredModels.map(model => <li key={model}><label className="settings-check"><input type="checkbox" disabled={draft.models.includes(model)} checked={draft.models.includes(model) || discoverySelection.includes(model)} onChange={event => setDiscoverySelection(current => event.target.checked ? [...current, model] : current.filter(item => item !== model))} /><code translate="no">{model}</code>{draft.models.includes(model) && <span>已添加</span>}</label></li>)}</ul>
        {!filteredModels.length && <p className="settings-hint">没有匹配的模型。</p>}<button type="button" disabled={!discoverySelection.length} onClick={() => { addModels(discoverySelection); setDiscoverySelection([]); }}>添加选中模型{discoverySelection.length > 0 && `（${discoverySelection.length}）`}</button>
      </fieldset>}
      {modelTestResults}
      </fieldset></div>
      <div className="settings-actions ai-provider-footer"><button type="button" className="primary" disabled={!dirty || !!invalid || busy} aria-busy={working === 'save'} onClick={() => { void save(); }}>保存供应商</button>{dirty && <span className="model-source-state">未保存</span>}</div>
      </fieldset>
      {priceInvalid && <p className="error-text" role="alert">单价须在 0 到 10000 之间。</p>}
      {draft.provider === 'llamacpp' && <>
        {editing ? <LocalAIRuntime providerId={!legacy ? editing.id : undefined} saved={{ ...saved, ...editing, provider_name: editing.name }} visible={visible} busy={busy} invalid={!!invalid} dirty={dirty} onStart={startModel} />
          : <p className="settings-hint">保存供应商后可启动本地模型。</p>}
        {launchDraft && saved.startup_defaults && saved.startup_command ? <LocalAIStartup key={draft.id || 'new'} onPicking={value => setWorking(value ? 'pick' : null)} value={launchDraft} defaults={saved.startup_defaults} model={draft.model} command={legacy || editing?.id === saved.active_provider_id ? saved.startup_command : ''} disabled={busy}
          onChange={value => { setLaunchDraft(value); update(value.port !== launchDraft.port ? { base_url: `http://127.0.0.1:${value.port}/v1` } : {}); }} />
          : <p className="error-text" role="alert">工作台后端尚未加载启动配置功能，请关闭旧工作台终端后重新打开 start-web.cmd；模型终端不用关闭。</p>}
      </>}
    </div>}
  </section>;
}
