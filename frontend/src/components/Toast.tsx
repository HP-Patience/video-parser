import { useEffect, useRef, useState } from 'react';

export type ToastPayload = { id: number; text: string; tone?: 'error' | 'info' };

// Visible for a moment, then a short exit animation, so the whole life stays under 5 seconds.
const LEAVE_AFTER = 4500;
const REMOVE_AFTER = 5000;

/**
 * A single transient notice layered above the page. It is fixed-positioned so a failure never
 * reflows the toolbar or the inspector, and it fades in and out instead of appearing abruptly.
 */
export default function Toast({ payload }: { payload: ToastPayload | null }) {
  const [shown, setShown] = useState<ToastPayload | null>(null);
  const [leaving, setLeaving] = useState(false);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    timers.current.forEach(window.clearTimeout);
    timers.current = [];
    if (!payload) return;
    setShown(payload);
    setLeaving(false);
    timers.current.push(window.setTimeout(() => setLeaving(true), LEAVE_AFTER));
    timers.current.push(window.setTimeout(() => { setShown(null); setLeaving(false); }, REMOVE_AFTER));
    return () => timers.current.forEach(window.clearTimeout);
  }, [payload]);

  if (!shown) return null;
  const tone = shown.tone === 'error' ? ' app-toast-error' : '';
  return <div className={`app-toast${tone} ${leaving ? 'is-leaving' : 'is-entering'}`}
    role="alert" aria-live="assertive">{shown.text}</div>;
}
