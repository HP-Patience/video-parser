import { useEffect, useRef, useState } from 'react';
import type { ReactNode, RefObject } from 'react';
import type { Media } from '../../lib/api';
import { chapterTime } from '../analysis/chapters';
import { backupKey, clearBackup, errorText, readBackup, studyPath, studyRequest, writeBackup } from './studyApi';
import type { PlaybackPoint } from './studyApi';
import './study.css';

type Props = {
  projectId: string; media: Media; videoRef: RefObject<HTMLVideoElement | null>; onSeek: (time: number) => void;
  /** The study sidebar lines its bottom up with this bar. */
  anchorRef: RefObject<HTMLElement | null>; children?: ReactNode;
  /** The study view keeps an empty anchor for layout; other views render nothing until there is an offer. */
  showEmpty?: boolean;
};
export default function PlaybackResume({ projectId, media, videoRef, onSeek, anchorRef, children, showEmpty = true }: Props) {
  const [offer, setOffer] = useState<number | null>(null);
  const [status, setStatus] = useState('正在读取播放位置…');
  const [error, setError] = useState('');
  const actions = useRef<{ choose: (time: number) => void; retry: () => void } | null>(null);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const controller = new AbortController(), url = studyPath(projectId, 'progress');
    const key = backupKey(projectId, media.resource, 'progress');
    let alive = true, ready = false, blocked = false, awaitingChoice = false, touched = false;
    let revision = 0, sending = false, pending: PlaybackPoint | null = null, lastSent = -1, lastSave = 0;
    const message = (text: string, failure = '') => { if (alive) { setStatus(text); setError(failure); } };
    const snapshot = (): PlaybackPoint => ({ position: Math.max(0, video.currentTime),
      duration: Number.isFinite(video.duration) && video.duration > 0 ? video.duration : media.duration, revision });
    const persist = async () => {
      if (!ready || blocked || sending || !pending) return;
      const point = { ...pending, revision }; pending = null; sending = true;
      writeBackup(key, point); message('正在保存播放位置…');
      try {
        const saved = await studyRequest<PlaybackPoint>(url, { resource: media.resource, position: point.position,
          duration: point.duration, expected_revision: revision });
        revision = saved.revision; lastSent = saved.position; lastSave = Date.now();
        if (!pending) { clearBackup(key); message('播放位置已保存'); }
      } catch (reason) { pending = pending || point; blocked = true; message('播放位置未保存', errorText(reason)); }
      finally { sending = false; if (pending && !blocked) void persist(); }
    };
    const queue = (force = false) => {
      if (!ready || awaitingChoice || !touched) return;
      const point = snapshot();
      if (!Number.isFinite(point.duration) || point.duration <= 0 || !Number.isFinite(point.position)) return;
      if (!force && Date.now() - lastSave < 5000) return;
      if (!sending && Math.abs(point.position - lastSent) < .1) return;
      pending = point; writeBackup(key, point); void persist();
    };
    const activate = () => { touched = true; awaitingChoice = false; if (alive) setOffer(null); };
    const play = () => { activate(); queue(true); };
    const seek = () => { if (video.currentTime > 0 || ready) { activate(); queue(true); } };
    const pause = () => queue(true);
    const tick = () => { if (!video.paused) queue(); };
    const leaving = () => queue(true);
    const visibility = () => { if (document.visibilityState === 'hidden') leaving(); };
    const load = async (retry = false) => {
      try {
        const saved = await studyRequest<PlaybackPoint>(url + '?resource=' + encodeURIComponent(media.resource), undefined, controller.signal);
        if (!alive) return;
        revision = saved.revision; lastSent = saved.position; ready = true; blocked = false;
        if (retry && touched) { touched = true; awaitingChoice = false; setOffer(null); queue(true); return; }
        const local = readBackup<PlaybackPoint>(key);
        const recover = local && local.revision === saved.revision && Number.isFinite(local.position) && local.position >= 0;
        const position = recover ? local.position : saved.position;
        const length = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : media.duration;
        if (!touched && position >= 1 && (!length || position < length)) {
          awaitingChoice = true; setOffer(position); message(recover ? '发现未同步的本机播放位置' : '已读取上次播放位置');
        } else { message('播放位置自动保存'); if (touched) queue(true); }
      } catch (reason) { if (alive && !controller.signal.aborted) message('播放位置读取失败', errorText(reason)); }
    };
    actions.current = { choose(time) { activate(); onSeek(time); pending = { ...snapshot(), position: time }; writeBackup(key, pending); void persist(); },
      retry() { void load(true); } };
    video.addEventListener('play', play); video.addEventListener('seeked', seek);
    video.addEventListener('pause', pause); video.addEventListener('ended', pause); video.addEventListener('timeupdate', tick);
    window.addEventListener('pagehide', leaving); document.addEventListener('visibilitychange', visibility);
    void load();
    return () => {
      queue(true); alive = false; controller.abort(); actions.current = null;
      video.removeEventListener('play', play); video.removeEventListener('seeked', seek);
      video.removeEventListener('pause', pause); video.removeEventListener('ended', pause); video.removeEventListener('timeupdate', tick);
      window.removeEventListener('pagehide', leaving); document.removeEventListener('visibilitychange', visibility);
    };
  }, [projectId, media.resource, media.preview_url, media.duration, videoRef]);
  // The effect is keyed by immutable media identity; onSeek only performs a guarded player operation.
  // The resume offer only appears until the learner chooses.
  if (!showEmpty && offer === null && !error && !children) return null;
  return <section ref={anchorRef} className={'playback-resume' + (offer === null && !error && !children ? ' is-empty' : '')} aria-label="播放进度">
    <div className="playback-resume-offer">
      {/* Routine saving is silent; only the resume offer and failures are shown. */}
      {offer !== null && <><p>上次学到 <strong>{chapterTime(offer)}</strong></p>
        <button type="button" onClick={() => actions.current?.choose(offer)}>继续上次学习 · {chapterTime(offer)}</button>
        <button type="button" onClick={() => actions.current?.choose(0)}>从头播放</button></>}
      {children}
    </div>
    {error && <p className="study-save-status" role="status">{status}</p>}
    {error && <div className="study-error" role="alert"><p>{error}</p><button type="button" onClick={() => actions.current?.retry()}>重试保存位置</button></div>}
  </section>;
}
