import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import Icon from './Icon';
import { formatClock } from '../lib/format';
import { readAutoNext, readRate, saveAutoNext, saveRate, SPEED_PRESETS } from '../lib/playback';
import './video-controls.css';

/** Idle delay before the bar fades out while playing. */
const HIDE_MS = 2200;
const VOLUME_STEP = 0.05;

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
  const [autoNext, setAutoNext] = useState(readAutoNext);
  const [idle, setIdle] = useState(false);
  // True once the pointer has left the video area: the bar then hides even while paused.
  const [away, setAway] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [speedOpen, setSpeedOpen] = useState(false);
  const [hover, setHover] = useState<{ left: number; seconds: number } | null>(null);
  const dragging = useRef(false);
  const idleTimer = useRef<number | null>(null);
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
      setAway(true); setIdle(false); setHover(null);
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

  function seek(seconds: number) {
    const video = videoRef.current;
    if (!video || !duration) return;
    video.currentTime = Math.max(0, Math.min(duration, seconds));
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

  const thumb = projectId && resource && duration > 0
    ? `/api/workspace/projects/${projectId}/preview-frame?resource=${encodeURIComponent(resource)}&time=${Math.max(0, Math.floor(hover?.seconds ?? 0))}`
    : '';

  return <div className={'video-controls' + (hidden ? ' is-hidden' : '') + (playing ? ' is-playing' : '')}
    onFocusCapture={() => setFocusWithin(byKeyboard.current)} onBlurCapture={event => {
      // Keep the bar up while focus stays somewhere inside it (tabbing between buttons).
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusWithin(false);
    }}>
    <div className="vc-track-wrap">
      <div className="vc-track" role="slider" aria-label="播放进度" tabIndex={0} aria-valuemin={0} aria-valuemax={Math.round(duration)} aria-valuenow={Math.round(time)}
        aria-valuetext={formatClock(time)}
        onPointerDown={event => { dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); seek(ratioAt(event) * duration); }}
        onPointerUp={event => { dragging.current = false; event.currentTarget.releasePointerCapture(event.pointerId); }}
        onPointerMove={event => {
          const ratio = ratioAt(event);
          const rect = event.currentTarget.getBoundingClientRect();
          setHover({ left: Math.max(0, Math.min(rect.width, event.clientX - rect.left)), seconds: ratio * duration });
          if (dragging.current) seek(ratio * duration);
        }}
        onPointerLeave={() => setHover(null)}
        onKeyDown={event => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
          event.preventDefault();
          seek(time + (event.key === 'ArrowRight' ? 5 : -5));
        }}>
        <div className="vc-played" style={{ width: `${percent}%` }} />
        <div className="vc-knob" style={{ left: `${percent}%` }} />
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
      <div className="vc-speed">
        <button type="button" className="vc-button vc-rate" onClick={() => setSpeedOpen(value => !value)}
          aria-expanded={speedOpen} aria-label="播放速度" title="播放速度">{`${rate}×`}</button>
        {speedOpen && <div className="vc-speed-menu" role="menu" aria-label="播放速度">
          {SPEED_PRESETS.map(preset => <button type="button" key={preset} role="menuitem" aria-pressed={preset === rate}
            onClick={() => chooseRate(preset)}>{`${preset}×`}</button>)}
          <label className="vc-auto-next">
            <input type="checkbox" checked={autoNext}
              onChange={event => { setAutoNext(event.target.checked); saveAutoNext(event.target.checked); }} />
            播放结束自动播放下一集
          </label>
        </div>}
      </div>
      <button type="button" className="vc-button" onClick={toggleFullscreen} aria-label="全屏" title="全屏"><Icon name="maximize" /></button>
    </div>
  </div>;
}
