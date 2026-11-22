import { useEffect, useState } from 'react';

export type ToastPayload = { id: number; text: string; tone?: 'error' | 'info' };

/* 第 016 期 · 轻提示
   什么时候该用 toast：它只适合"操作已经生效、只需一句确认"的短句。
   需要用户做决定的（选画质、补字幕、同意条款）不走这里，留在原地。

   所以这里管的是三件事：
   1) 停留时间跟内容重量走——错误要多读几秒，普通信息不该霸屏；
   2) 鼠标悬停时不倒数，正在读的文字不能自己消失；
   3) 错误用 assertive 会打断读屏，普通信息用 polite 排队念。 */

const STAY = { error: 6000, info: 4000 } as const;
const EXIT = 220;

export default function Toast({ payload, onDismiss }: { payload: ToastPayload | null; onDismiss?: () => void }) {
  const [shown, setShown] = useState<ToastPayload | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [hovered, setHovered] = useState(false);

  useEffect(() => {
    if (!payload) return;
    setShown(payload);
    setLeaving(false);
  }, [payload]);

  useEffect(() => {
    if (!shown || hovered) return;
    const stay = STAY[shown.tone === 'error' ? 'error' : 'info'];
    const fade = window.setTimeout(() => setLeaving(true), stay);
    const clear = window.setTimeout(() => { setShown(null); setLeaving(false); }, stay + EXIT);
    return () => { window.clearTimeout(fade); window.clearTimeout(clear); };
  }, [shown, hovered]);

  if (!shown) return null;
  const error = shown.tone === 'error';
  return <div key={shown.id}
    className={`app-toast${error ? ' app-toast-error' : ''} ${leaving ? 'is-leaving' : 'is-entering'}${onDismiss ? ' is-dismissable' : ''}`}
    role={error ? 'alert' : 'status'} aria-live={error ? 'assertive' : 'polite'}
    onMouseEnter={onDismiss ? () => setHovered(true) : undefined}
    onMouseLeave={onDismiss ? () => setHovered(false) : undefined}
    onClick={onDismiss}>{shown.text}{onDismiss && <span className="app-toast-dismiss" aria-hidden="true">×</span>}</div>;
}
