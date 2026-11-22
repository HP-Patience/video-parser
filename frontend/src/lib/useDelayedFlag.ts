import { useEffect, useRef, useState } from 'react';

/**
 * 第 017 期 · 延迟出现的加载态。
 *
 * 两个动作，缺一个都会让界面"闪"：
 * - 加载超过 delay 才真的显示占位：多数请求几十毫秒就回来，立刻画骨架等于闪一下白条；
 * - 一旦显示了，至少留 minVisible：否则 80ms 的骨架比不显示更让人以为出错。
 *
 * 返回 true 表示"现在该显示占位了"。
 */
export function useDelayedFlag(active: boolean, delay = 300, minVisible = 400) {
  const [visible, setVisible] = useState(false);
  const shownAt = useRef(0);

  useEffect(() => {
    if (active) {
      if (visible) return;
      const timer = window.setTimeout(() => { shownAt.current = Date.now(); setVisible(true); }, delay);
      return () => window.clearTimeout(timer);
    }
    if (!visible) return;
    const remaining = Math.max(0, minVisible - (Date.now() - shownAt.current));
    const timer = window.setTimeout(() => setVisible(false), remaining);
    return () => window.clearTimeout(timer);
  }, [active, visible, delay, minVisible]);

  return visible;
}
