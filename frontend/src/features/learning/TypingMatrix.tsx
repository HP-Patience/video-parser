import { useEffect, useRef, useState } from 'react';

/* 第 012 期 · 长回复：AI 在打字时不该只有一个转圈图标。
   这里是一根被拨动的弦——基频加一个更轻的倍频——画成 5×5 的点阵：
   每一列算一个位移，再让最靠近那条曲线的点最亮，于是点阵里有一条真的在抖的线。

   亮度是时间的连续函数，不是"开 / 关"闪烁，所以看起来像有余振而不是在闪灯。
   prefers-reduced-motion 时停在某一帧，不再逐帧重算。 */

const COLS = 5;
const ROWS = 5;
const RATE = 1.6;      // 每秒周期数，接近真人打字时手指的节奏
const DAMPING = 0.55;  // 越接近 0 衰减越慢；这里只要一点余振，不要一直抖

type Dot = { column: number; row: number };

const DOTS: Dot[] = Array.from({ length: COLS * ROWS }, (_, index) => ({
  column: index % COLS,
  row: Math.floor(index / COLS),
}));

/** 某一列在 t 秒时的位移（-1..1 附近），由两个模式叠加而成。 */
function displacement(column: number, t: number) {
  const x = (column + 1) / (COLS + 1);
  const envelope = Math.exp(-DAMPING * (t % 1.1));
  return envelope * (Math.sin(Math.PI * x) * Math.cos(2 * Math.PI * RATE * t)
    + Math.sin(2 * Math.PI * x) * Math.cos(4 * Math.PI * RATE * t) / 3);
}

export default function TypingMatrix({ label = 'AI 正在输入' }: { label?: string }) {
  const [time, setTime] = useState(0);
  const still = useRef(false);

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => { still.current = query.matches; };
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);

  useEffect(() => {
    if (still.current) { setTime(0.32); return; }
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      setTime((now - start) / 1000);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  return <span className="ai-chat-matrix" role="img" aria-label={label}>
    {DOTS.map(dot => {
      // 把位移映射到行号：位移为 0 时线在中间，摆动时整条线在点阵里上下走。
      const target = (ROWS - 1) / 2 - displacement(dot.column, time) * (ROWS - 1) / 2;
      const near = Math.max(0, 1 - Math.abs(dot.row - target) / 0.95);
      const opacity = 0.12 + near * 0.88;
      return <i key={`${dot.column}-${dot.row}`} style={{ opacity }}
        className={near > 0.55 ? 'is-lit' : undefined} />;
    })}
  </span>;
}
