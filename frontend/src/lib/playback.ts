/** Playback speed, play mode and auto-advance preferences. Browser-local only; nothing is sent to the server. */

const RATE_KEY = 'video-parser:playback-rate';
const AUTO_NEXT_KEY = 'video-parser:auto-next';
const PLAY_MODE_KEY = 'video-parser:play-mode';

export const SPEED_PRESETS = [0.75, 1, 1.25, 1.5, 1.75, 2];
export const MIN_RATE = 0.25;
export const MAX_RATE = 4;

/** Keeps an arbitrary typed value inside the range browsers actually honour. */
export function normalizeRate(value: number) {
  if (!Number.isFinite(value)) return 1;
  return Math.min(MAX_RATE, Math.max(MIN_RATE, Math.round(value * 100) / 100));
}

export function readRate() {
  try { const saved = Number(localStorage.getItem(RATE_KEY)); return saved ? normalizeRate(saved) : 1; } catch { return 1; }
}

export function saveRate(rate: number) {
  try { localStorage.setItem(RATE_KEY, String(normalizeRate(rate))); } catch { /* Optional browser preference. */ }
}

/** Auto-advance is on unless the learner turned it off. */
export function readAutoNext() {
  try { return localStorage.getItem(AUTO_NEXT_KEY) !== 'off'; } catch { return true; }
}

export function saveAutoNext(enabled: boolean) {
  try { localStorage.setItem(AUTO_NEXT_KEY, enabled ? 'on' : 'off'); } catch { /* Optional browser preference. */ }
}

/**
 * What a finished video does next. `list` continues the episode catalog in order and stops on the
 * last one, `loop` replays the current video, `shuffle` jumps to a random other episode.
 */
export type PlayMode = 'list' | 'loop' | 'shuffle';

export const PLAY_MODES: { id: PlayMode; label: string; hint: string }[] = [
  { id: 'list', label: '列表播放', hint: '按分集顺序继续，最后一集播完停止' },
  { id: 'loop', label: '循环播放', hint: '当前视频播完从头再来' },
  { id: 'shuffle', label: '随机播放', hint: '播完随机换一个分集' },
];

/** Ordered playback is the default, which matches the previous always-advance behaviour. */
export function readPlayMode(): PlayMode {
  try {
    const saved = localStorage.getItem(PLAY_MODE_KEY) as PlayMode | null;
    return PLAY_MODES.some(mode => mode.id === saved) ? saved as PlayMode : 'list';
  } catch { return 'list'; }
}

export function savePlayMode(mode: PlayMode) {
  try { localStorage.setItem(PLAY_MODE_KEY, mode); } catch { /* Optional browser preference. */ }
}

/** Play mode labels are also used by the player for its aria/title text. */
export function playModeLabel(mode: PlayMode) {
  return PLAY_MODES.find(item => item.id === mode)?.label || PLAY_MODES[0].label;
}
