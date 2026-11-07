import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, RefObject } from 'react';
import type { Media } from '../../lib/api';
import Icon from '../../components/Icon';
import ChapterTimeline from '../analysis/ChapterTimeline';
import type { AnalysisTimeline } from '../analysis/chapters';
import TimestampNotes from './TimestampNotes';
import AIChat from './AIChat';
import type { AIChatProps } from './AIChat';
import './study-sidebar.css';
import SubtitlePanel from './SubtitlePanel';
import type { SubtitlePanelProps } from './SubtitlePanel';

type Props = {
  projectId: string; media: Media; videoRef: RefObject<HTMLVideoElement | null>;
  timeline: AnalysisTimeline | null; enabled: boolean; collapsed: boolean;
  onToggle: () => void; onSeek: (time: number) => void; assistant: AIChatProps;
  /** alignTo is the playback bar whose bottom the sidebar matches. */
  subtitles?: SubtitlePanelProps;
  active: boolean; alignTo: RefObject<HTMLElement | null>;
};
const tabs = [{ id: 'chapters', label: '章节' }, { id: 'notes', label: '个人笔记' }, { id: 'chat', label: 'AI 对话' }, { id: 'subtitles', label: '字幕' }] as const;
type Tab = typeof tabs[number]['id'];
const WIDE = '(min-width: 1171px)';
const HEIGHT_KEY = 'video-parser:study-sidebar-height';
/** Below this the tabs and the composer no longer fit without clipping. */
const MIN_HEIGHT = 340;
const HEIGHT_STEP = 24;

function clampHeight(value: number, min: number, max: number) { return Math.min(Math.max(value, min), Math.max(min, max)); }

/** A height the reader dragged in; null keeps the sidebar aligned with the playback bar. */
function savedHeight() {
  try {
    const value = Math.round(Number(localStorage.getItem(HEIGHT_KEY)));
    return Number.isFinite(value) && value >= MIN_HEIGHT ? value : null;
  } catch { return null; }
}

export default function StudySidebar({ projectId, media, videoRef, timeline, enabled, collapsed, onToggle, onSeek, assistant, alignTo, active, subtitles }: Props) {
  const [tab, setTab] = useState<Tab>('chapters');
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const aside = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const positions = useRef<Partial<Record<Tab, number>>>({});
  const manualHeight = useRef<number | null>(savedHeight());
  const [resizeHeight, setResizeHeight] = useState<number | null>(manualHeight.current);
  const realign = useRef<() => void>(() => {});
  const heightDrag = useRef<{ y: number; height: number; min: number; max: number } | null>(null);
  useLayoutEffect(() => { positions.current = {}; }, [projectId, media.resource]);
  useLayoutEffect(() => {
    const element = body.current;
    if (element) element.scrollTop = positions.current[tab] ?? (tab === 'chat' ? element.scrollHeight : 0);
  }, [tab, projectId, media.resource, collapsed, active]);
  function show(id: Tab) { setTab(id); }
  useEffect(() => {
    const sidebar = aside.current, anchor = alignTo.current, panel = anchor?.closest('.preview-panel');
    if (!sidebar || !anchor || !panel || collapsed) return;
    const wide = window.matchMedia(WIDE);
    const reset = () => { delete sidebar.dataset.aligned; sidebar.style.removeProperty('--study-sidebar-height'); };
    const apply = (height: number) => { sidebar.dataset.aligned = ''; sidebar.style.setProperty('--study-sidebar-height', `${height}px`); };
    const align = () => {
      if (!wide.matches) { reset(); return; }
      // A height dragged in by the reader wins over the bar alignment until it is reset.
      if (manualHeight.current !== null) { apply(manualHeight.current); return; }
      // Unrounded: rounding left the sidebar up to 1px longer than the bar.
      const height = anchor.getBoundingClientRect().bottom - sidebar.getBoundingClientRect().top;
      if (height <= 0) { reset(); return; }
      apply(height);
    };
    realign.current = align;
    // The panel resizes when the video loads or the bar wraps; the sidebar itself is never observed (no feedback loop).
    const observer = new ResizeObserver(align);
    observer.observe(panel); wide.addEventListener('change', align); align();
    return () => { observer.disconnect(); wide.removeEventListener('change', align); reset(); };
  }, [alignTo, collapsed, media.resource, projectId, active]);
  // Keep the separator's aria value in step with the height the alignment produced.
  useEffect(() => {
    const height = aside.current?.getBoundingClientRect().height;
    if (height) setResizeHeight(Math.round(height));
  }, [projectId, media.resource, collapsed]);
  useEffect(() => () => { document.body.classList.remove('is-resizing-study-sidebar'); }, []);
  function heightBounds() {
    const sidebar = aside.current;
    if (!sidebar) return { min: MIN_HEIGHT, max: MIN_HEIGHT };
    const rect = sidebar.getBoundingClientRect();
    return { min: Math.min(rect.height, MIN_HEIGHT), max: Math.max(rect.height, window.innerHeight - rect.top - 16) };
  }
  function commitHeight(height: number) {
    manualHeight.current = height;
    setResizeHeight(height);
    try { localStorage.setItem(HEIGHT_KEY, String(height)); } catch { /* Optional browser preference. */ }
  }
  function startHeightDrag(event: PointerEvent<HTMLDivElement>) {
    const sidebar = aside.current;
    if (event.button !== 0 || !sidebar) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    heightDrag.current = { y: event.clientY, height: sidebar.getBoundingClientRect().height, ...heightBounds() };
    document.body.classList.add('is-resizing-study-sidebar');
  }
  function moveHeightDrag(event: PointerEvent<HTMLDivElement>) {
    const drag = heightDrag.current, sidebar = aside.current;
    if (!drag || !sidebar) return;
    sidebar.style.setProperty('--study-sidebar-height', `${Math.round(clampHeight(drag.height + event.clientY - drag.y, drag.min, drag.max))}px`);
  }
  function endHeightDrag(event: PointerEvent<HTMLDivElement>) {
    const drag = heightDrag.current, sidebar = aside.current;
    heightDrag.current = null;
    document.body.classList.remove('is-resizing-study-sidebar');
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    // A plain click on the grip must not pin the height and drop the bar alignment.
    if (drag && sidebar && Math.abs(sidebar.getBoundingClientRect().height - drag.height) >= 1) commitHeight(Math.round(sidebar.getBoundingClientRect().height));
  }
  function resetHeight() {
    manualHeight.current = null;
    try { localStorage.removeItem(HEIGHT_KEY); } catch { /* Optional browser preference. */ }
    realign.current();
    const height = aside.current?.getBoundingClientRect().height;
    setResizeHeight(height ? Math.round(height) : null);
  }
  function keyHeight(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Home' || event.key === 'Enter') { event.preventDefault(); resetHeight(); return; }
    const direction = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
    const sidebar = aside.current;
    if (!direction || !sidebar) return;
    event.preventDefault();
    const { min, max } = heightBounds();
    const height = Math.round(clampHeight(sidebar.getBoundingClientRect().height + direction * HEIGHT_STEP, min, max));
    sidebar.dataset.aligned = '';
    sidebar.style.setProperty('--study-sidebar-height', `${height}px`);
    commitHeight(height);
  }
  function move(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === 'ArrowRight' ? (index+1)%tabs.length : event.key === 'ArrowLeft' ? (index+tabs.length-1)%tabs.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : null;
    if (next === null) return;
    event.preventDefault(); show(tabs[next].id); buttons.current[next]?.focus();
  }
  return <aside ref={aside} className={'study-sidebar' + (collapsed ? ' is-collapsed' : '')} aria-label="学习侧栏">
    <div className="study-sidebar-heading"><h2 className={collapsed ? undefined : 'sr-only'}>学习侧栏</h2>
      <button type="button" className="study-sidebar-toggle" onClick={onToggle} aria-expanded={!collapsed}
        aria-controls="learning-sidebar-content project-context" aria-label={collapsed ? '展开学习侧栏' : '收起学习侧栏'}
        title={collapsed ? '展开学习侧栏' : '收起学习侧栏'}><Icon name={collapsed ? 'book-open' : 'x'} /></button></div>
    <div id="learning-sidebar-content" hidden={collapsed}>
      <div className="study-sidebar-tabs" role="tablist" aria-label="学习侧栏内容">{tabs.map((item,index) =>
        <button type="button" key={item.id} ref={element => { buttons.current[index] = element; }}
          id={`study-tab-${item.id}`} role="tab" aria-selected={tab === item.id} aria-controls={`study-panel-${item.id}`}
          tabIndex={tab === item.id ? 0 : -1} onClick={() => show(item.id)} onKeyDown={event => move(event,index)}>{item.label}</button>)}</div>
      <div className="study-sidebar-body" data-tab={tab} ref={body} onScroll={event => { if (!collapsed && active && event.currentTarget.getClientRects().length) positions.current[tab] = event.currentTarget.scrollTop; }}>
        <div id="study-panel-chapters" role="tabpanel" aria-labelledby="study-tab-chapters" hidden={tab !== 'chapters'}>
          {timeline ? <ChapterTimeline timeline={timeline} videoRef={videoRef} mediaKey={`${projectId}:${media.preview_url}`}
            duration={media.duration} enabled={enabled} view="directory" visible={!collapsed && tab === 'chapters'} onSeek={onSeek} />
            : <p className="study-sidebar-empty">暂无章节目录，生成“章节时间轴”后可在这里跳转。</p>}
        </div>
        <div id="study-panel-notes" role="tabpanel" aria-labelledby="study-tab-notes" hidden={tab !== 'notes'}>
          {/* Hidden rather than unmounted: switching tabs or collapsing must not lose the editor/draft. */}
          <TimestampNotes key={`${projectId}:${media.resource}`} projectId={projectId} media={media} videoRef={videoRef} onSeek={onSeek} />
        </div>
        <div id="study-panel-subtitles" role="tabpanel" aria-labelledby="study-tab-subtitles" hidden={tab !== 'subtitles'}>
          {subtitles && <SubtitlePanel {...subtitles} />}
        </div>
        <div id="study-panel-chat" role="tabpanel" aria-labelledby="study-tab-chat" hidden={tab !== 'chat'}>
          <AIChat key={`chat:${projectId}:${media.resource}`} {...assistant} videoRef={videoRef} duration={media.duration} onSeek={onSeek}
            floating visible={active && !collapsed && tab === 'chat'} pickerVisible={active && (collapsed || tab === 'chat')} onReveal={() => { if (collapsed) onToggle(); show('chat'); }} />
        </div>
      </div>
    </div>
    <div className="study-sidebar-resizer" role="separator" aria-orientation="horizontal" aria-label="调整学习侧栏高度"
      aria-valuenow={resizeHeight ?? MIN_HEIGHT} aria-valuemin={MIN_HEIGHT}
      aria-valuemax={Math.round(Math.max(resizeHeight ?? MIN_HEIGHT, window.innerHeight))}
      aria-valuetext={resizeHeight ? `侧栏 ${resizeHeight} 像素高` : undefined} tabIndex={0}
      title="拖动调整侧栏高度，双击恢复与播放条对齐"
      onPointerDown={startHeightDrag} onPointerMove={moveHeightDrag} onPointerUp={endHeightDrag}
      onPointerCancel={endHeightDrag} onLostPointerCapture={endHeightDrag} onKeyDown={keyHeight}
      onDoubleClick={resetHeight} />
  </aside>;
}
