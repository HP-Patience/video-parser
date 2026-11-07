import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import './study-splitter.css';

/** The rail may never grow past this share of the workspace: the player keeps 55%. */
const RAIL_SHARE = 0.45;
const STORAGE_KEY = 'video-parser:study-rail-width';
const DEFAULT_WIDTH = 336;
const MIN_WIDTH = 280;
const MAX_WIDTH = 560;
const STEP = 24;

function clamp(value: number, min: number, max: number) { return Math.min(Math.max(value, min), Math.max(min, max)); }

function savedWidth() {
  try {
    const value = Math.round(Number(localStorage.getItem(STORAGE_KEY)));
    return Number.isFinite(value) && value >= MIN_WIDTH ? clamp(value, MIN_WIDTH, MAX_WIDTH) : DEFAULT_WIDTH;
  } catch { return DEFAULT_WIDTH; }
}

/** Divider between the player column and the study rail: drag, arrow keys or double-click to reset. */
export default function StudySplitter() {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [width, setWidth] = useState(savedWidth);
  const workspace = () => handle.current?.closest<HTMLElement>('.study-workspace') ?? null;
  const bounds = () => {
    const size = workspace()?.clientWidth ?? 0;
    return { min: MIN_WIDTH, max: Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.floor(size * RAIL_SHARE))) };
  };
  const railWidth = () => Math.round(handle.current?.closest<HTMLElement>('.study-context-rail')?.getBoundingClientRect().width ?? DEFAULT_WIDTH);
  const apply = (value: number) => { workspace()?.style.setProperty('--study-rail-width', `${value}px`); };
  // The committed width lives in a CSS variable, so pointer moves never re-render the study view.
  useLayoutEffect(() => {
    apply(width);
    try { localStorage.setItem(STORAGE_KEY, String(width)); } catch { /* Optional browser preference. */ }
  }, [width]);
  useEffect(() => () => { document.body.classList.remove('is-resizing-study-rail'); }, []);
  function start(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, width: railWidth() };
    document.body.classList.add('is-resizing-study-rail');
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const origin = drag.current;
    if (!origin) return;
    const { min, max } = bounds();
    apply(clamp(origin.width - (event.clientX - origin.x), min, max));
  }
  function end(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    drag.current = null;
    document.body.classList.remove('is-resizing-study-rail');
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setWidth(railWidth());
  }
  function keydown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Home') { event.preventDefault(); setWidth(DEFAULT_WIDTH); return; }
    const direction = event.key === 'ArrowLeft' ? 1 : event.key === 'ArrowRight' ? -1 : 0;
    if (!direction) return;
    event.preventDefault();
    const { min, max } = bounds();
    setWidth(value => clamp(value + direction * STEP, min, max));
  }
  return <div ref={handle} className="study-splitter" role="separator" aria-orientation="vertical"
    aria-label="调整视频区与侧栏宽度" aria-valuenow={width} aria-valuemin={MIN_WIDTH} aria-valuemax={MAX_WIDTH}
    aria-valuetext={`侧栏 ${width} 像素宽`} tabIndex={0} title="拖动调整视频区与侧栏宽度，双击恢复默认"
    onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end}
    onKeyDown={keydown} onDoubleClick={() => setWidth(DEFAULT_WIDTH)} />;
}
