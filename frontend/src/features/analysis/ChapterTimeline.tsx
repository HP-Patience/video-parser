import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, RefObject } from 'react';
import { chapterTime } from './chapters';
import type { AnalysisTimeline } from './chapters';
import './chapter-timeline.css';
import '../learning/subtitle-panel.css';
import useChapterScroll from './useChapterScroll';
import MathText, { mathTextLabel } from '../../components/MathText';

type Props = { timeline: AnalysisTimeline; videoRef: RefObject<HTMLVideoElement | null>; mediaKey: string;
  duration: number; enabled: boolean; view: 'player' | 'directory'; compact?: boolean; visible?: boolean; onSeek: (seconds: number) => void };

export default function ChapterTimeline({ timeline, videoRef, mediaKey, duration, enabled, view, compact = false, visible = true, onSeek }: Props) {
  const list = useRef<HTMLOListElement>(null);
  const [playback, setPlayback] = useState({ time: 0, duration: 0 });
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const update = () => setPlayback({ time: Number.isFinite(video.currentTime) ? video.currentTime : 0,
      duration: Number.isFinite(video.duration) ? video.duration : 0 });
    update();
    for (const name of ['loadedmetadata', 'durationchange', 'timeupdate', 'seeking', 'seeked', 'emptied']) video.addEventListener(name, update);
    return () => { for (const name of ['loadedmetadata', 'durationchange', 'timeupdate', 'seeking', 'seeked', 'emptied']) video.removeEventListener(name, update); };
  }, [videoRef, mediaKey]);
  const total = playback.duration || duration;
  const chapters = timeline.chapters.filter(chapter => !total || chapter.time < total);
  const scale = total || (chapters.at(-1)?.time || 0) + 1;
  const active = enabled ? chapters.reduce((found, chapter, index) => chapter.time <= playback.time ? index : found, -1) : -1;
  const { pauseFollow } = useChapterScroll(list, active, view === 'directory' && visible, `${mediaKey}:${timeline.recordId}:${chapters.length}`, videoRef);
  if (!chapters.length) return view === 'directory' ? <p className="study-sidebar-empty">没有落在当前视频时长内的章节时间点。</p> : null;
  const current = active >= 0 ? chapters[active] : null;
  const end = current ? (chapters[active + 1]?.time || total) : 0;
  const currentRange = current ? `${chapterTime(current.time)}${end > current.time ? ` — ${chapterTime(end)}` : ''}` : '';
  const seek = (time: number) => {
    if (!enabled) return;
    onSeek(time);
    setPlayback(previous => ({ ...previous, time }));
  };
  const canScrub = enabled && Number.isFinite(total) && total > 0;
  const position = canScrub ? Math.min(total, Math.max(0, playback.time)) : 0;
  const seekTrack = (event: MouseEvent<HTMLDivElement>) => {
    if (!canScrub) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width > 0) seek(Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)) * total);
  };
  const seekTrackKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!canScrub || event.altKey || event.ctrlKey || event.metaKey) return;
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? total
      : ['ArrowRight', 'ArrowUp'].includes(event.key) ? position + 5
      : ['ArrowLeft', 'ArrowDown'].includes(event.key) ? position - 5 : null;
    if (target === null) return;
    event.preventDefault(); event.stopPropagation(); seek(Math.min(total, Math.max(0, target)));
  };
  const introduction = <p className="chapter-timeline-intro-body" translate={current?.description ? 'no' : undefined}>{current ? current.description ? <MathText text={current.description} /> : '这条分析记录仅提供章节标题，暂无详细介绍。'
    : enabled ? '播放到章节起点，或点击圆点查看这一章的介绍。' : '当前分析记录对应另一份视频，不与此视频同步介绍。'}</p>;
  return <section className={`chapter-timeline chapter-timeline-${view}${compact && view === 'player' ? ' is-compact' : ''}`} aria-labelledby={view === 'player' ? 'chapter-timeline-title-player' : undefined} aria-label={view === 'directory' ? '章节目录' : undefined}>
    {view === 'player' && <div className="chapter-timeline-heading"><h3 id="chapter-timeline-title-player" title={timeline.label}>AI 章节时间线</h3></div>}
    {view === 'player' && <>
    <div className="chapter-timeline-track" role="group" aria-label="点击章节跳转视频">
      <div className="chapter-timeline-scrubber" role="slider" aria-label="视频时间定位"
        aria-valuemin={0} aria-valuemax={canScrub ? total : 0} aria-valuenow={position}
        aria-valuetext={canScrub ? `${chapterTime(position)} / ${chapterTime(total)}` : '暂不可定位'}
        aria-disabled={!canScrub} tabIndex={canScrub ? 0 : -1} onClick={seekTrack} onKeyDown={seekTrackKey} />
      {enabled && <span className="chapter-timeline-progress" aria-hidden="true"
        style={{ width: `${Math.min(100, Math.max(0, playback.time / scale * 100))}%` }} />}
      {chapters.map((chapter, index) => <button type="button" className="chapter-timeline-marker" key={chapter.time} disabled={!enabled}
        style={{ left: `${chapter.time / scale * 100}%` }}
        aria-label={`跳到 ${chapterTime(chapter.time)}：${mathTextLabel(chapter.title)}`} aria-current={active === index ? 'true' : undefined}
        title={`${chapterTime(chapter.time)} · ${mathTextLabel(chapter.title)}`} onClick={() => seek(chapter.time)}><span className="chapter-timeline-dot" aria-hidden="true" /></button>)}
    </div>
    {/* Shown once playback reaches a chapter (or when the record belongs to another video); nothing before that. */}
    <section className="chapter-timeline-intro" aria-label="当前章节介绍" aria-live="polite" aria-atomic="true" hidden={!current && enabled}>
      <div className="chapter-timeline-intro-header">
        {current && !compact && <span className="chapter-timeline-intro-meta">{`当前章节 · ${active + 1} / ${chapters.length}`}</span>}
        <div className="chapter-timeline-current-heading" data-range={currentRange}>
          {current && <time>{currentRange}</time>}
          <h4 translate={current ? 'no' : undefined}>{current ? <MathText text={current.title} /> : '请切换到对应视频'}</h4>
        </div>
      </div>
      {/* In the compact player the chapter counter rides at the start of the description,
          so the row above carries only the time range and the chapter title. */}
      {current && (compact ? <div className="chapter-timeline-intro-line">
        <span className="chapter-timeline-intro-meta">{`当前章节 · ${active + 1} / ${chapters.length}`}</span>
        {introduction}
      </div> : introduction)}
    </section>
    </>}
    {view === 'directory' && <>
    <ol ref={list} className="chapter-timeline-list" aria-label="章节列表" tabIndex={0}
      onWheel={pauseFollow} onTouchMove={pauseFollow} onKeyDown={event => {
        if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(event.key)) pauseFollow();
      }}>{chapters.map((chapter, index) => <li key={chapter.time}>
      <button type="button" disabled={!enabled} aria-current={active === index ? 'true' : undefined} onClick={() => seek(chapter.time)}>
        <time>{chapterTime(chapter.time)}</time><MathText text={chapter.title} /></button>
    </li>)}</ol>
    {!enabled && <p className="chapter-timeline-hint">这条分析记录对应另一份视频；切换到对应归档视频后可跳转。</p>}
    </>}
  </section>;
}
