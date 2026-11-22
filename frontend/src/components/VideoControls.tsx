import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import Icon from './Icon';
import { Segmented } from './ChoiceControls';
import type { IconName } from './Icon';
import { formatClock } from '../lib/format';
import { workspaceUrl } from '../lib/api-base';
import { MAX_RATE, MIN_RATE, normalizeRate, PLAY_MODES, playModeLabel, readPlayMode, readRate, savePlayMode, saveRate, SPEED_PRESETS } from '../lib/playback';
import type { PlayMode } from '../lib/playback';
import './video-controls.css';

/** Idle delay before the bar fades out while playing. */
const HIDE_MS = 2200;
const VOLUME_STEP = 0.05;
/** Scrub thumbnails cost one ffmpeg run each, so a moving pointer waits this long before asking. */
const THUMB_DELAY_MS = 140;
/** A drag fires far more pointer events than a video can seek to; keep the seek rate human. */
const SEEK_INTERVAL_MS = 80;
const MODE_ICONS: Record<PlayMode, IconName> = { list: 'list-video', loop: 'repeat-1', shuffle: 'shuffle' };

type Props = {
  videoRef: RefObject<HTMLVideoElement | null>;
  /** Needed for the hover thumbnails; without them the bar still works, just no preview image. */
  projectId?: string;
  resource?: string;
  mediaKey: string;
};

export default function VideoControls({ videoRef, projectId, resource, mediaKey }: Props) {
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [rate, setRate] = useState(readRate);
  const [mode, setMode] = useState<PlayMode>(readPlayMode);
  const [idle, setIdle] = useState(false);
  // True once the pointer has left the video area: the bar then hides even while paused.
  const [away, setAway] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [speedOpen, setSpeedOpen] = useState(false);
  // The custom field starts at the current rate, so small nudges need one edit, not a full retype.
  const [draft, setDraft] = useState('');
  const [hover, setHover] = useState<{ left: number; seconds: number } | null>(null);
  const [thumbSecond, setThumbSecond] = useState<number | null>(null);
  // While dragging, the knob follows the pointer instead of waiting for the video's timeupdate.
  const [dragPercent, setDragPercent] = useState<number | null>(null);
  const speedBox = useRef<HTMLDivElement>(null);
  const speedMenu = useRef<HTMLDivElement>(null), rateButton = useRef<HTMLButtonElement>(null);
  const dragging = useRef(false);
  const idleTimer = useRef<number | null>(null);
  const thumbTimer = useRef<number | null>(null);
  const wantedThumb = useRef<number | null>(null);
  const lastSeek = useRef(0);
  const pendingSeek = useRef<number | null>(null);
  // Keyboard focus keeps the bar up; a click does not, so the bar can still hide after playback starts.
  const byKeyboard = useRef(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const sync = () => {
      setPlaying(!video.paused && !video.ended);
      setTime(video.currentTime || 0);
      setDuration(Number.isFinite(video.duration) ? video.duration : 0);
      setVolume(video.volume);
      setMuted(video.muted);
      setRate(video.playbackRate);
    };
    sync();
    const names = ['play', 'pause', 'timeupdate', 'durationchange', 'volumechange', 'ratechange',
      'ended', 'seeking', 'seeked', 'loadedmetadata'];
    names.forEach(name => video.addEventListener(name, sync));
    return () => names.forEach(name => video.removeEventListener(name, sync));
  }, [videoRef, mediaKey]);

  // Looping is native and seamless here; the ended handlers in App own list and shuffle instead.
  useEffect(() => {
    const video = videoRef.current;
    if (video) video.loop = mode === 'loop';
  }, [videoRef, mediaKey, mode]);

  // A new video invalidates whatever thumbnail was pending, and the timer must not outlive the bar.
  useEffect(() => {
    wantedThumb.current = null;
    setThumbSecond(null);
    return () => { if (thumbTimer.current) clearTimeout(thumbTimer.current); };
  }, [mediaKey]);

  // An open speed menu closes on an outside click or Escape.
  // 第 009 期：它是一块非模态浮层，但也必须有完整的焦点路径——打开时把焦点送进去，
  // 关闭时还给触发按钮。否则键盘用户按 Esc 后焦点会掉回页面顶部，得重新 Tab 一圈。
  useEffect(() => {
    if (!speedOpen) return;
    speedMenu.current?.querySelector<HTMLElement>('button, input')?.focus();
    const close = () => {
      const inside = !!speedMenu.current?.contains(document.activeElement);
      setSpeedOpen(false);
      if (inside) rateButton.current?.focus();
    };
    const dismiss = (event: Event) => {
      const target = event.target as Node | null;
      if (target && speedBox.current?.contains(target)) return;
      close();
    };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape); };
  }, [speedOpen]);

  // Pointer over the video wakes the bar and keeps it up; leaving the video hides it right away.
  // Idle fading while playing still applies for a pointer that sits still on the video.
  useEffect(() => {
    const stage = videoRef.current?.closest('.media-stage');
    function wake() {
      setAway(false);
      setIdle(false);
      if (idleTimer.current) clearTimeout(idleTimer.current);
      idleTimer.current = window.setTimeout(() => setIdle(true), HIDE_MS);
    }
    function leave() {
      if (idleTimer.current) clearTimeout(idleTimer.current);
      setAway(true); setIdle(false); setHover(null); clearThumb();
    }
    function keys() { byKeyboard.current = true; wake(); }
    function press() { byKeyboard.current = false; wake(); }
    if (stage) {
      stage.addEventListener('pointerenter', wake);
      stage.addEventListener('pointermove', wake);
      stage.addEventListener('pointerdown', press);
      stage.addEventListener('pointerleave', leave);
    }
    window.addEventListener('keydown', keys);
    return () => {
      if (stage) {
        stage.removeEventListener('pointerenter', wake);
        stage.removeEventListener('pointermove', wake);
        stage.removeEventListener('pointerdown', press);
        stage.removeEventListener('pointerleave', leave);
      }
      window.removeEventListener('keydown', keys);
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [videoRef, mediaKey]);

  const hidden = (away || idle && playing) && !speedOpen && !hover && !focusWithin;
  const percent = duration > 0 ? Math.min(100, (time / duration) * 100) : 0;
  const shownPercent = dragPercent ?? percent;

  function seek(seconds: number) {
    const video = videoRef.current;
    if (!video || !duration) return;
    video.currentTime = Math.max(0, Math.min(duration, seconds));
  }

  /** Throttled seek for dragging: the knob still tracks the pointer, the video catches up. */
  function requestSeek(seconds: number) {
    pendingSeek.current = seconds;
    const now = performance.now();
    if (now - lastSeek.current < SEEK_INTERVAL_MS) return;
    lastSeek.current = now;
    pendingSeek.current = null;
    seek(seconds);
  }

  function endDrag(seconds: number | null) {
    dragging.current = false;
    setDragPercent(null);
    if (seconds === null) return;
    pendingSeek.current = null;
    seek(seconds);
  }

  function toggle() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused || video.ended) void video.play().catch(() => undefined);
    else video.pause();
  }

  function setVolumeTo(next: number) {
    const video = videoRef.current;
    if (!video) return;
    video.volume = Math.max(0, Math.min(1, next));
    video.muted = video.volume === 0;
  }

  function chooseRate(next: number) {
    const video = videoRef.current;
    setSpeedOpen(false);
    saveRate(next);
    if (video) video.playbackRate = next;
  }

  /** Typed rates are clamped to what browsers honour; anything unusable leaves the rate alone. */
  function applyDraft() {
    const typed = Number(draft);
    if (!Number.isFinite(typed) || typed <= 0) return;
    chooseRate(normalizeRate(typed));
  }

  /** One icon is enough: each click steps to the next mode, the hover tip names the current one. */
  function cycleMode() {
    const index = PLAY_MODES.findIndex(item => item.id === mode);
    const next = PLAY_MODES[(index + 1) % PLAY_MODES.length].id;
    setMode(next);
    savePlayMode(next);
    // Apply immediately so the ended handler never sees the stale flag.
    const video = videoRef.current;
    if (video) video.loop = next === 'loop';
  }

  function toggleFullscreen() {
    const stage = videoRef.current?.closest('.media-stage');
    if (!stage) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void (stage as HTMLElement).requestFullscreen?.().catch(() => undefined);
  }

  function ratioAt(event: React.PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return rect.width ? Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) : 0;
  }

  /** Ask for a thumbnail only once the pointer settles, so a drag does not queue a frame per pixel. */
  function queueThumb(seconds: number) {
    const second = Math.max(0, Math.floor(seconds));
    if (second === wantedThumb.current) return;
    const entering = wantedThumb.current === null;
    wantedThumb.current = second;
    if (thumbTimer.current) clearTimeout(thumbTimer.current);
    if (entering) { setThumbSecond(second); return; }
    thumbTimer.current = window.setTimeout(() => {
      if (wantedThumb.current === second) setThumbSecond(second);
    }, THUMB_DELAY_MS);
  }

  function clearThumb() {
    if (thumbTimer.current) clearTimeout(thumbTimer.current);
    wantedThumb.current = null;
    setThumbSecond(null);
  }

  const thumb = projectId && resource && duration > 0 && thumbSecond !== null
    ? workspaceUrl(`/projects/${projectId}/preview-frame?resource=${encodeURIComponent(resource)}&time=${thumbSecond}`)
    : '';

  return <div className={'video-controls' + (hidden ? ' is-hidden' : '') + (playing ? ' is-playing' : '')}
    onFocusCapture={() => setFocusWithin(byKeyboard.current)} onBlurCapture={event => {
      // Keep the bar up while focus stays somewhere inside it (tabbing between buttons).
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusWithin(false);
    }}>
    <div className="vc-track-wrap">
      <div className="vc-track" role="slider" aria-label="播放进度" tabIndex={0} aria-valuemin={0} aria-valuemax={Math.round(duration)} aria-valuenow={Math.round(time)}
        aria-valuetext={formatClock(time)}
        onPointerDown={event => {
          dragging.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
          const ratio = ratioAt(event);
          setDragPercent(ratio * 100);
          lastSeek.current = 0;
          seek(ratio * duration);
        }}
        onPointerUp={event => {
          event.currentTarget.releasePointerCapture(event.pointerId);
          endDrag(pendingSeek.current ?? ratioAt(event) * duration);
        }}
        onPointerCancel={() => endDrag(pendingSeek.current)}
        onPointerMove={event => {
          const ratio = ratioAt(event);
          const rect = event.currentTarget.getBoundingClientRect();
          const seconds = ratio * duration;
          setHover({ left: Math.max(0, Math.min(rect.width, event.clientX - rect.left)), seconds });
          queueThumb(seconds);
          if (!dragging.current) return;
          setDragPercent(ratio * 100);
          requestSeek(seconds);
        }}
        onPointerLeave={() => { setHover(null); clearThumb(); }}
        onKeyDown={event => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
          event.preventDefault();
          seek(time + (event.key === 'ArrowRight' ? 5 : -5));
        }}>
        <div className="vc-played" style={{ width: `${shownPercent}%` }} />
        <div className="vc-knob" style={{ left: `${shownPercent}%` }} />
      </div>
      {hover && duration > 0 && <div className="vc-preview" style={{ left: hover.left }}>
        {thumb && <img src={thumb} alt="" draggable={false} />}
        <span>{formatClock(hover.seconds)}</span>
      </div>}
    </div>
    <div className="vc-bar">
      <button type="button" className="vc-button" onClick={toggle} aria-label={playing ? '暂停' : '播放'} title={playing ? '暂停' : '播放'}>
        <Icon name={playing ? 'pause' : 'play'} />
      </button>
      <span className="vc-time">{formatClock(time)} / {formatClock(duration)}</span>
      <div className="vc-spacer" />
      <div className="vc-volume">
        <button type="button" className="vc-button" onClick={() => { const video = videoRef.current; if (video) video.muted = !video.muted; }}
          aria-label={muted || volume === 0 ? '取消静音' : '静音'} title={muted || volume === 0 ? '取消静音' : '静音'}>
          <Icon name={muted || volume === 0 ? 'volume-x' : 'volume-2'} />
        </button>
        <input type="range" aria-label="音量" min={0} max={1} step={VOLUME_STEP} value={muted ? 0 : volume}
          onChange={event => setVolumeTo(Number(event.target.value))} />
      </div>
      <button type="button" className="vc-button vc-mode" onClick={cycleMode} title={playModeLabel(mode)}
        aria-label={`播放模式：${playModeLabel(mode)}，点击切换`} data-mode={mode}>
        <Icon name={MODE_ICONS[mode]} />
      </button>
      <div className="vc-speed" ref={speedBox}>
        <button type="button" ref={rateButton} className="vc-button vc-rate"
          onClick={() => { setDraft(String(rate)); setSpeedOpen(value => !value); }}
          aria-expanded={speedOpen} aria-label="播放速度" title="播放速度">{`${rate}×`}</button>
        {speedOpen && <div ref={speedMenu} className="vc-menu vc-speed-menu" role="dialog" aria-label="播放速度">
          <Segmented label="常用速度" columns={3} value={rate} onChange={chooseRate}
            options={SPEED_PRESETS.map(preset => ({ value: preset, label: `${preset}×` }))} />
          <div className="vc-speed-custom">
            <input type="number" inputMode="decimal" aria-label="自定义速度" min={MIN_RATE} max={MAX_RATE} step="0.05"
              placeholder={`${MIN_RATE}–${MAX_RATE}`} value={draft}
              onChange={event => setDraft(event.target.value)}
              onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); applyDraft(); } }} />
            <button type="button" aria-label="应用自定义速度" onClick={applyDraft}>应用</button>
          </div>
        </div>}
      </div>
      <button type="button" className="vc-button" onClick={toggleFullscreen} aria-label="全屏" title="全屏"><Icon name="maximize" /></button>
    </div>
  </div>;
}
