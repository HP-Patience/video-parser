import { useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { AISettings, Configuration } from '../../lib/api';
import { DEFAULT_SETTINGS } from './settings';
import type { Settings } from './settings';
import CacheManager from './CacheManager';
import AISettingsSection from './AISettingsSection';
import LearningTargets from './LearningTargets';
import EmbeddingSettings from './EmbeddingSettings';
import UsageDashboard from './UsageDashboard';
import ArchiveHealthPanel from './ArchiveHealth';
import UpdatePanel from './UpdatePanel';
import { setLanguage, useLanguage } from '../../lib/i18n';
import type { Language } from '../../lib/i18n';
import './settings-page.css';

const groups = { general: '常用设置', ai: 'AI 模型', usage: '用量看板', health: '归档体检', maintenance: '存储与维护' } as const;
const LANGUAGES: [Language, string, string][] = [['zh', '中文', 'zh-CN'], ['en', 'English', 'en']];
type Group = keyof typeof groups;
function groupFromURL(): Group {
  const value = new URLSearchParams(location.search).get('settings_section');
  return value && Object.hasOwn(groups, value) ? value as Group : 'general';
}
type Props = { settings: Settings; onChange: (value: Settings) => void; config: Configuration | null;
  configError: string; saveStatus: string; activeCount: number | null; onAIChange: (value: AISettings) => void;
  onArchiveRootChange: (archiveRoot: string) => void;
  visible: boolean; focusAIRequest: number; focusAITarget?: 'api' | 'sources' };

export default function SettingsPage({ settings, onChange, config, configError, saveStatus, activeCount, onAIChange, onArchiveRootChange, visible, focusAIRequest, focusAITarget = 'api' }: Props) {
  const [group, setGroup] = useState<Group>(() => focusAIRequest ? 'ai' : groupFromURL());
  const [aiVisited, setAIVisited] = useState(() => group === 'ai');
  const language = useLanguage();
  const [focusAI, setFocusAI] = useState(false);
  const [choosingLibrary, setChoosingLibrary] = useState(false);
  const [libraryNotice, setLibraryNotice] = useState('');
  // /config only carries ai_configured; the full AI settings come from the AI section (config.ai is set after a save).
  const [loadedAI, setLoadedAI] = useState<AISettings | null>(null);
  const previousRequest = useRef(0);
  useEffect(() => {
    if (focusAIRequest && focusAIRequest !== previousRequest.current) {
      previousRequest.current = focusAIRequest; setGroup('ai'); setAIVisited(true); setFocusAI(true);
      const url = new URL(location.href); url.searchParams.set('settings_section', 'ai'); history.replaceState(null, '', url);
    }
  }, [focusAIRequest]);
  useEffect(() => {
    const restore = () => { if (new URLSearchParams(location.search).get('page') === 'settings') setGroup(groupFromURL()); };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);
  useEffect(() => { if (group === 'ai') setAIVisited(true); }, [group]);
  function choose(next: Group) {
    setGroup(next); setFocusAI(false);
    const url = new URL(location.href);
    if (next === 'general') url.searchParams.delete('settings_section'); else url.searchParams.set('settings_section', next);
    history.pushState(null, '', url);
    requestAnimationFrame(() => document.getElementById('settings-group-' + next)?.focus({ preventScroll: true }));
  }
  async function chooseLibrary() {
    setChoosingLibrary(true); setLibraryNotice('');
    try {
      const result = await request<{ path: string | null; archive_root: string; project_count: number }>(
        '/library/pick', new AbortController().signal, {});
      if (!result.path) { setLibraryNotice('没有选择文件夹。'); return; }
      onArchiveRootChange(result.archive_root);
      try {
        localStorage.removeItem('video-parser:last-project');
        localStorage.removeItem('video-parser:last-quality');
      } catch { /* Reload still resets the current workspace. */ }
      setLibraryNotice(`已切换到 ${result.archive_root}，检测到 ${result.project_count} 个项目；正在刷新应用。`);
      window.setTimeout(() => location.reload(), 700);
    } catch (reason) {
      setLibraryNotice(reason instanceof Error ? reason.message : '无法更改视频库。');
    } finally {
      setChoosingLibrary(false);
    }
  }
  return <div className="settings-page">
    <nav className="settings-section-nav" aria-label="设置分类">{Object.entries(groups).map(([name, label]) => <button type="button" key={name}
      aria-current={group === name ? 'page' : undefined} onClick={() => choose(name as Group)}>{label}</button>)}</nav>
    <div hidden={group !== 'general'}>
      <h2 className="settings-group-title" id="settings-group-general" tabIndex={-1}>常用设置</h2>
      <div className="settings-card-grid settings-general-grid">
        <section className="settings-card settings-card-wide" aria-labelledby="settings-language"><h3 id="settings-language">界面语言</h3>
          <div className="language-switch" role="radiogroup" aria-labelledby="settings-language">{LANGUAGES.map(([value, label, lang]) =>
            <label key={value} className={language === value ? 'checked' : ''} translate="no" lang={lang}>
              <input type="radio" name="interface-language" value={value} checked={language === value} onChange={() => setLanguage(value)} />{label}</label>)}</div>
        </section>
        <section className="settings-card" aria-labelledby="settings-preferences"><h3 id="settings-preferences">下载偏好</h3>
          <label htmlFor="default-quality">默认画质</label>
          <select id="default-quality" value={settings.qualityPreference} onChange={event => onChange({ ...settings, qualityPreference: event.target.value as Settings['qualityPreference'] })}>
            <option value="best">最高可用</option><option value="1080p">1080P 高清</option><option value="720p">720P 准高清</option>
            <option value="480p">480P 标清</option><option value="360p">360P 流畅</option>
          </select>
          <label className="settings-check"><input type="checkbox" checked={settings.completionReminder} onChange={event => onChange({ ...settings, completionReminder: event.target.checked })} />归档完成提醒</label>
          {saveStatus && <p role="status" className="settings-save-status" aria-live="polite">{saveStatus}</p>}
        </section>
        <section className="settings-card" aria-labelledby="settings-transcription"><h3 id="settings-transcription">本地转写</h3>
          <label className="settings-check"><input type="checkbox" checked={settings.reuseTranscriptionModel} onChange={event => onChange({ ...settings, reuseTranscriptionModel: event.target.checked })} />连续任务复用模型</label>
          <p className="settings-hint">本机 CUDA 模型（large-v3-turbo）空闲 60 秒后释放，不会切换到 CPU 或云端。</p>
        </section>
      </div>
      <div className="settings-actions"><button type="button" title="仅恢复常用偏好，不影响 AI 配置和归档数据" onClick={() => onChange({ ...DEFAULT_SETTINGS })}>恢复常用默认</button></div>
    </div>
    <div hidden={group !== 'ai'}>
      <h2 className="settings-group-title sr-only" id="settings-group-ai" tabIndex={-1}>AI 模型</h2>
      {(group === 'ai' || aiVisited) && <div className="settings-section-stack">
        <LearningTargets api={config?.ai ?? loadedAI} focusRequest={focusAI && visible && focusAITarget === 'sources' ? focusAIRequest : 0}>
          <AISettingsSection onChange={onAIChange} onLoad={setLoadedAI} autoFocus={focusAI && visible && focusAITarget === 'api'} visible={visible && group === 'ai'} />
        </LearningTargets>
        <EmbeddingSettings />
      </div>}
    </div>
    <div hidden={group !== 'usage'}>
      <h2 className="settings-group-title" id="settings-group-usage" tabIndex={-1}>用量看板</h2>
      <div className="settings-card settings-card-wide"><UsageDashboard visible={visible && group === 'usage'} /></div>
    </div>
    <div hidden={group !== 'health'}>
      <h2 className="settings-group-title" id="settings-group-health" tabIndex={-1}>归档体检</h2>
      <div className="settings-card settings-card-wide"><ArchiveHealthPanel visible={visible && group === 'health'} /></div>
    </div>
    <div hidden={group !== 'maintenance'}>
      <h2 className="settings-group-title" id="settings-group-maintenance" tabIndex={-1}>存储与维护</h2>
      <div className="settings-card-grid settings-maintenance-grid">
        <section className="settings-card" aria-labelledby="settings-storage"><h3 id="settings-storage">归档位置</h3>
          <code className="archive-root">{config?.archive_root || (configError ? '无法读取归档位置' : '正在读取…')}</code>
          <p className="settings-hint">公共视频库可在网页版、桌面版和不同 Windows 用户间共享。切换前请停止正在运行的任务。</p>
          {config?.archive_root_source === 'environment' && <p className="settings-hint">当前由 VIDEO_LIBRARY_ROOT 环境变量固定，移除该变量后才能在这里更改。</p>}
          {config?.archive_root_source === 'legacy' && <p className="settings-hint">当前仍使用旧版安装目录中的视频库；选择公共目录后会切换到新位置。</p>}
          <div className="settings-actions"><button type="button" disabled={!config || choosingLibrary || config.archive_root_source === 'environment'} onClick={() => { void chooseLibrary(); }}>
            {choosingLibrary ? '正在选择…' : '更改视频库'}
          </button></div>
          {libraryNotice && <p className="settings-hint" role="status">{libraryNotice}</p>}
          <p className="settings-hint">缓存清理不影响已归档内容。</p>
        </section>
        <section className="settings-card" aria-labelledby="settings-services"><h3 id="settings-services">服务状态</h3>
          {config ? <dl className="service-status">
            <dt>媒体工具：</dt><dd>{config.media_ready ? '就绪' : '未就绪'}</dd>
            <dt>字幕：</dt><dd>{config.subtitles_ready ? '就绪' : '未就绪'}</dd>
            <dt>转录：</dt><dd>{config.transcription_ready ? '就绪' : '未安装'}</dd>
            <dt>AI 连接配置：</dt><dd>{config.ai_configured ? '已配置' : '未配置'}</dd>
            {config.version && <><dt>版本：</dt><dd>{config.version}</dd></>}
          </dl> : <p className="settings-hint">{configError || '正在读取…'}</p>}
        </section>
        <UpdatePanel currentVersion={config?.version || ''} />
        <div className="settings-card settings-card-wide"><CacheManager activeCount={activeCount} /></div>
      </div>
    </div>
  </div>;
}
