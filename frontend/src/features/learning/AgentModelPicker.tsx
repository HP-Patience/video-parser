import type { AgentTarget } from '../../lib/api';
import type { AgentModelsState } from './useAgentModels';
import './agent-models.css';

const valueOf = (target: Pick<AgentTarget, 'engine' | 'model'>) => target.engine + '|' + encodeURIComponent(target.model);
const ENGINES = [{ id: 'claude', name: 'Claude Code' }, { id: 'codex', name: 'Codex' }, { id: 'opencode', name: 'OpenCode' }, { id: 'dsh', name: 'DeepSeek Harness' }] as const;

type Props = { state: AgentModelsState; disabled?: boolean; onManage?: (button: HTMLButtonElement) => void };
export default function AgentModelPicker({ state, disabled = false }: Props) {
  const selected = state.selected;
  const source = state.catalog?.engines.find(item => item.id === selected.engine);
  const currentKnown = !selected.model || source?.models.some(item => item.id === selected.model);
  const locked = disabled || state.refreshing;
  // Only targets enabled in Settings are offered; the current choice always stays visible so the select remains truthful.
  const shown = (target: Pick<AgentTarget, 'engine' | 'model'>) => state.enabled(target) || valueOf(target) === valueOf(selected);
  function choose(value: string) {
    const [engine, model] = value.split('|');
    if (['api', 'claude', 'codex', 'opencode', 'dsh'].includes(engine || '')) {
      state.select({ engine: engine as AgentTarget['engine'], model: decodeURIComponent(model || ''), effort: '' });
    }
  }
  return <div className="agent-controls">
    <div className="agent-model-row">
      <select aria-label="引擎与模型" value={valueOf(selected)} disabled={locked} onChange={event => choose(event.target.value)}>
        <optgroup label="API"><option value="api|" translate={state.apiModel ? 'no' : 'yes'}>{state.apiModel ? 'API · ' + state.apiModel : 'API（模型设置）'}</option></optgroup>
        {ENGINES.map(engine => {
          const entry = state.catalog?.engines.find(item => item.id === engine.id);
          const models = entry?.models.filter(model => shown({ engine: engine.id, model: model.id })) || [];
          const engineDefault = shown({ engine: engine.id, model: '' });
          const unlisted = selected.engine === engine.id && !currentKnown;
          if (!engineDefault && !models.length && !unlisted) return null;
          return <optgroup key={engine.id} label={engine.name} disabled={!entry?.installed || entry.enabled === false}>
            {engineDefault && <option value={engine.id + '|'}>{engine.name} · 跟随工具默认模型</option>}
            {models.map(model => <option key={model.id} value={valueOf({ engine: engine.id, model: model.id })} title={model.id} translate="no">{engine.name + ' · ' + model.label}</option>)}
            {unlisted && <option value={valueOf(selected)} translate="no">{engine.name + ' · ' + selected.model}</option>}
          </optgroup>;
        })}
      </select>
      {state.efforts.length > 0 && <select className="agent-effort" aria-label="推理强度" value={selected.effort} disabled={locked}
        onChange={event => state.select({ ...selected, effort: event.target.value })}>
        <option value="">默认强度</option>{state.efforts.map(level => <option key={level} translate="no" value={level}>{level}</option>)}
      </select>}

    </div>
    {source?.enabled === false && <p className="agent-error" role="alert">所选 Agent 已关闭，请在设置中开启或明确选择其他工具。</p>}
    {state.error && <p className="agent-error" role="alert">{state.error}</p>}
  </div>;
}
