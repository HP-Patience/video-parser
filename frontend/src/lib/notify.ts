/**
 * One line of feedback for actions taken far from the toast host.
 *
 * The toast lives in App, so a title edited inside a grid card only needs to shout
 * here instead of threading a callback through every panel.
 */
export type NotifyTone = 'error' | 'info';

export function notify(text: string, tone: NotifyTone = 'info') {
  if (!text) return;
  window.dispatchEvent(new CustomEvent('video-parser:toast', { detail: { text, tone } }));
}
