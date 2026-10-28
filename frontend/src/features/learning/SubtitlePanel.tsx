import MathText from '../../components/MathText';
import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { request } from '../../lib/api';
import type { TranscriptOption } from '../../lib/api';
import './subtitle-panel.css';
type Data = { resource: string; sources: TranscriptOption[]; cues: { time: number; text: string }[] };
type Saved = { resource: string; filename: string; edited: number; cues: number };
export type SubtitlePanelProps = { projectId: string; mediaKey: string; videoRef: RefObject<HTMLVideoElement | null>; onSeek: (time: number) => void; onGenerate: () => void; version?: number };
const stamp = (time: number) => `${Math.floor(time / 60)}:${String(Math.floor(time % 60)).padStart(2, '0')}`;
/** Drafts are keyed by source resource and cue start time, the only stable keys across reloads. */
const draftKey = (source: string, time: number) => `${source}\u0000${time}`;
const tidy = (text: string) => text.replace(/\r\n/g, '\n').split('\n').map(line => line.trim()).filter(Boolean).join('\n');
export default function SubtitlePanel({ projectId, mediaKey, videoRef, onSeek, onGenerate, version = 0 }: SubtitlePanelProps) {
  const [data, setData] = useState<Data | null>(null), [resource, setResource] = useState(''), [error, setError] = useState('');
  const [reload, setReload] = useState(0), [current, setCurrent] = useState(0), [follow, setFollow] = useState(true);
  const [seekVersion, setSeekVersion] = useState(0);
  // Proofreading stays local until the user publishes one new "-校对" version; the source file is never rewritten.
  const [drafts, setDrafts] = useState<Record<string, string>>({}), [editing, setEditing] = useState<{ time: number; value: string } | null>(null);
  const [saving, setSaving] = useState(false), [editError, setEditError] = useState(''), [notice, setNotice] = useState('');
  const [help, setHelp] = useState(false);
  const helpBox = useRef<HTMLSpanElement>(null);
  const publishing = useRef<AbortController | null>(null);
  const focusBack = useRef<number | null>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => { setResource(''); setData(null); setCurrent(0); setFollow(true); }, [projectId, mediaKey]);
  useEffect(() => { setEditing(null); setEditError(''); }, [resource, projectId, mediaKey]);
  useEffect(() => () => publishing.current?.abort(), []);
  // The save confirmation is a corner toast, not a line inside the rail; it clears itself.
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 3000);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    const controller = new AbortController(); setError('');
    request<Data>(`/projects/${projectId}/subtitle-cues?resource=${encodeURIComponent(resource)}`, controller.signal).then(setData)
      .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '读取字幕失败'); });
    return () => controller.abort();
  }, [projectId, mediaKey, resource, reload, version]);
  useEffect(() => {
    const video = videoRef.current; if (!video) return;
    const update = () => setCurrent(video.currentTime);
    // Listen on the media element, so native scrubbing, keyboard and timestamp seeks
    // all resume follow. Ordinary playback must not override manual subtitle browsing.
    const seeking = () => { update(); setFollow(true); setSeekVersion(value => value + 1); };
    // A user may pause follow while a seek is buffering; completion must respect that.
    const seeked = () => { update(); setSeekVersion(value => value + 1); };
    video.addEventListener('timeupdate', update);
    video.addEventListener('seeking', seeking); video.addEventListener('seeked', seeked); update();
    return () => {
      video.removeEventListener('timeupdate', update);
      video.removeEventListener('seeking', seeking); video.removeEventListener('seeked', seeked);
    };
  }, [videoRef, projectId, mediaKey]);
  useEffect(() => {
    const refresh = () => setReload(value => value + 1);
    window.addEventListener('video-parser:subtitles-changed', refresh);
    return () => window.removeEventListener('video-parser:subtitles-changed', refresh);
  }, []);
  // The help popover replaces a permanent hint line under the toolbar, so the cue list starts higher.
  useEffect(() => {
    if (!help) return;
    const dismiss = (event: MouseEvent) => { if (!helpBox.current?.contains(event.target as Node)) setHelp(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setHelp(false); };
    document.addEventListener('mousedown', dismiss);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', dismiss); document.removeEventListener('keydown', escape); };
  }, [help]);
  let active = -1;
  data?.cues.forEach((cue, i) => { if (cue.time <= current) active = i; });
  useEffect(() => {
    const element = list.current;
    // Never scroll the row being proofread out of view while the video keeps playing.
    if (!follow || !element || editing) return;
    const align = () => {
      const row = element.querySelector<HTMLElement>('[aria-current="true"]');
      if (row && element.clientHeight > 0) element.scrollTop = Math.max(0, row.offsetTop - element.clientHeight / 3);
    };
    align();
    // The subtitle tab can be hidden when seeking. Align again when it becomes visible
    // or reflows, including a paused video whose active cue has not changed.
    const observer = new ResizeObserver(align); observer.observe(element);
    return () => observer.disconnect();
  }, [active, follow, seekVersion, data, editing]);
  // Closing the editor returns focus to the same cue, so keyboard proofreading keeps its place.
  useEffect(() => {
    const time = focusBack.current;
    if (time === null || editing) return;
    focusBack.current = null;
    list.current?.querySelector<HTMLElement>(`button[data-cue="${time}"]`)?.focus();
  }, [editing]);
  const source = data?.resource || '';
  const draftOf = (time: number) => drafts[draftKey(source, time)];
  const pending = Object.keys(drafts).filter(key => key.startsWith(source + '\u0000')).length;
  const begin = (time: number) => {
    const cue = data?.cues.find(item => item.time === time); if (!cue) return;
    setFollow(false); setEditError(''); setNotice('');
    setEditing({ time, value: draftOf(time) ?? cue.text });
  };
  const close = () => { focusBack.current = editing?.time ?? null; setEditing(null); setEditError(''); };
  const commit = () => {
    if (!editing) return;
    const cue = data?.cues.find(item => item.time === editing.time); if (!cue) return;
    const value = tidy(editing.value);
    if (!value) { setEditError('单条字幕不能为空；如需删除整条，请保留原文后另行处理。'); return; }
    setDrafts(current => {
      const next = { ...current }, key = draftKey(source, cue.time);
      if (value === cue.text) delete next[key]; else next[key] = value;
      return next;
    });
    close();
  };
  async function publish() {
    if (!source || !pending) return;
    publishing.current?.abort();
    const controller = new AbortController(); publishing.current = controller;
    setSaving(true); setEditError(''); setNotice('');
    const prefix = source + '\u0000';
    try {
      const saved = await request<Saved>(`/projects/${projectId}/subtitle-edits`, controller.signal,
        { resource: source, edits: Object.entries(drafts).filter(([key]) => key.startsWith(prefix))
          .map(([key, text]) => ({ time: Number(key.slice(prefix.length)), text })) });
      if (controller.signal.aborted) return;
      setDrafts(current => Object.fromEntries(Object.entries(current).filter(([key]) => !key.startsWith(prefix))));
      setNotice('已保存校对版本');
      setResource(saved.resource);
      window.dispatchEvent(new Event('video-parser:subtitles-changed'));
    } catch (reason) {
      if (!controller.signal.aborted) setEditError(reason instanceof Error ? reason.message : '保存校对版本失败，请重试。');
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }
  // The publish notice sits outside the panel: inside, it was pinned under the last cue and read as part of the list.
  return <>
  <section className="subtitle-scroll-panel" aria-label="滚动字幕">
    <strong className="sr-only">字幕</strong>
    {error && <p role="alert">{error}</p>}
    {!data?.cues.length ? <p>暂无字幕。<button type="button" onClick={onGenerate}>生成字幕</button></p> : <>
      <div className="subtitle-toolbar"><select aria-label="字幕版本" value={data.resource} onChange={e => { setNotice(''); setResource(e.target.value); }}>{data.sources.map(source => <option key={source.resource} value={source.resource}>{source.label}</option>)}</select>
      <button type="button" aria-pressed={follow} onClick={() => setFollow(!follow)}>{follow ? '自动跟随：开启' : '恢复自动跟随'}</button>
      {pending > 0 && <><button type="button" className="primary" aria-busy={saving} disabled={saving} onClick={() => { void publish(); }}>{saving ? '正在保存…' : `保存为新版本（${pending} 处修改）`}</button>
        <button type="button" disabled={saving} onClick={() => { setDrafts(current => Object.fromEntries(Object.entries(current).filter(([key]) => !key.startsWith(source + '\u0000')))); setEditError(''); }}>放弃修改</button></>}
      <span className="subtitle-help" ref={helpBox}>
        <button type="button" className="subtitle-help-trigger" aria-label="校对说明" title="校对说明"
          aria-expanded={help} aria-controls="subtitle-help" onClick={() => setHelp(value => !value)}>?</button>
        {help && <span id="subtitle-help" className="subtitle-help-popover" role="note">
          双击（或按 F2）任意条目可校对；保存会新增「-校对」版本，原字幕文件不变。</span>}
      </span></div>
      <div className="subtitle-scroll-list" ref={list} tabIndex={0} onWheel={() => setFollow(false)} onTouchMove={() => setFollow(false)} onKeyDown={e => { if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(e.key)) setFollow(false); }}>
        {data.cues.map((cue, i) => {
          if (editing && editing.time === cue.time) return <div className="subtitle-cue-editor" key={`edit-${i}`}>
            <time>{stamp(cue.time)}</time>
            <textarea aria-label={`校对第 ${i + 1} 条字幕`} rows={2} maxLength={1000} autoFocus value={editing.value}
              onChange={event => setEditing({ time: cue.time, value: event.target.value })}
              onKeyDown={event => {
                if (event.key === 'Escape') { event.preventDefault(); close(); }
                else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); commit(); }
              }} />
            <div className="subtitle-cue-actions">
              <button type="button" className="primary" onClick={commit}>完成</button>
              <button type="button" onClick={close}>取消</button>
              <span>Ctrl+Enter 完成 · Esc 取消</span>
            </div>
          </div>;
          return <button type="button" key={i} data-cue={cue.time} aria-current={active === i ? 'true' : undefined}
            onClick={() => onSeek(cue.time)} onDoubleClick={() => begin(cue.time)} onKeyDown={event => { if (event.key === 'F2') { event.preventDefault(); begin(cue.time); } }}>
            <time>{stamp(cue.time)}</time><MathText text={draftOf(cue.time) ?? cue.text} />
            {draftOf(cue.time) !== undefined && <span className="subtitle-draft-mark">未保存</span>}
          </button>;
        })}
      </div>
      {editError && <p className="error-text" role="alert">{editError}</p>}
    </>}
  </section>
  {notice && <p className="subtitle-toast" role="status">{notice}</p>}
  </>;
}
