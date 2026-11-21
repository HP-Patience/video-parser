import { useCallback, useEffect, useState } from 'react';

// 主题只切换 <html data-theme>；颜色映射全部在 styles/tokens.css。
// 存储键与 index.html 的首帧脚本一致：localStorage 优先，cookie 兜底。
// 只有 localStorage 时，一旦浏览器把它清掉或禁掉（隐私模式、清理站点数据、站点分区策略），
// 写入会被 try/catch 静默吞掉，表现就是“切了白天，刷新又回黑色”。cookie 通常还在，用它兜住。
export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'vp-theme';
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;
const DARK_QUERY = '(prefers-color-scheme: dark)';

const asTheme = (value: string | null | undefined): Theme | null =>
  (value === 'light' || value === 'dark' ? value : null);

function cookieTheme(): Theme | null {
  try {
    const match = document.cookie.match(/(?:^|;\s*)vp-theme=(light|dark)/);
    return asTheme(match?.[1]);
  } catch {
    return null;
  }
}

function storedTheme(): Theme | null {
  try {
    const value = asTheme(localStorage.getItem(STORAGE_KEY));
    if (value) return value;
  } catch { /* 落到 cookie */ }
  return cookieTheme();
}

function rememberTheme(value: Theme) {
  try { localStorage.setItem(STORAGE_KEY, value); } catch { /* 由 cookie 兜底 */ }
  try {
    document.cookie = `${STORAGE_KEY}=${value}; path=/; max-age=${COOKIE_MAX_AGE}; SameSite=Lax`;
  } catch { /* 两条通道都不可用时只在本次会话生效 */ }
}

function systemTheme(): Theme {
  return window.matchMedia?.(DARK_QUERY).matches ? 'dark' : 'light';
}

function initialTheme(): Theme {
  const current = document.documentElement.dataset.theme;
  if (current === 'light' || current === 'dark') return current;
  return storedTheme() ?? systemTheme();
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // 用户未手动选择时，跟随系统主题变化。
  useEffect(() => {
    const query = window.matchMedia?.(DARK_QUERY);
    if (!query) return;
    const onChange = () => { if (!storedTheme()) setTheme(query.matches ? 'dark' : 'light'); };
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  // 另一个标签页切换主题时保持一致，避免同一浏览器里两种配色并存。
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY) return;
      const next = asTheme(event.newValue);
      if (next) setTheme(next);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const toggleTheme = useCallback(() => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark';
    rememberTheme(next);
    setTheme(next);
  }, [theme]);

  return { theme, toggleTheme };
}
