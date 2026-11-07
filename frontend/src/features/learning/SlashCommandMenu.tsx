import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { SlashCommand } from './slashCommands';

type Props = {
  id: string; commands: SlashCommand[]; selected: number; refreshing: boolean; hasCommands: boolean;
  onPick: (command: SlashCommand) => void; onSelect: (index: number) => void;
};
type Place = 'above' | 'below' | 'overlay';

export default function SlashCommandMenu({ id, commands, selected, refreshing, hasCommands, onPick, onSelect }: Props) {
  const menu = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ mode: 'above' as Place, max: 190 });
  // Float above the textarea; flip below only when the visible room above (under the sticky top bar
  // and any clipping scroll container) is too small. An empty conversation's composer fills the rail,
  // so when neither side has room the list floats inside the composer's blank space instead.
  useLayoutEffect(() => {
    const box = menu.current, field = box?.parentElement?.querySelector('textarea');
    if (!box || !field) return;
    const update = () => {
      const rect = field.getBoundingClientRect();
      let top = Math.max(0, document.querySelector('.app-topbar')?.getBoundingClientRect().bottom ?? 0), bottom = window.innerHeight;
      for (let node = box.parentElement?.parentElement; node && node !== document.body; node = node.parentElement) {
        if (!/(auto|scroll|hidden|clip)/.test(getComputedStyle(node).overflowY)) continue;
        const clip = node.getBoundingClientRect(); top = Math.max(top, clip.top); bottom = Math.min(bottom, clip.bottom);
      }
      const chrome = 58, above = rect.top - top - chrome, below = bottom - rect.bottom - chrome;
      const side = above >= below ? 'above' as Place : 'below' as Place, room = Math.max(above, below);
      // An empty conversation's composer grows to fill the rail, leaving no room on either side.
      // Float the list inside that blank space instead of clipping it down to a single row.
      const mode = room >= 160 ? side : rect.height >= 150 ? 'overlay' as Place : side;
      const cap = Math.min(320, Math.round(window.innerHeight * .48));
      // In the composer the list is bottom-aligned: keep its heading below the clip top (54px is
      // the menu's own heading/padding), so a scrolled page cannot hide it under the top bar.
      const overlay = Math.min(cap, Math.round(rect.height - 54), Math.round(rect.bottom - top - 49));
      const max = mode === 'overlay' ? Math.max(0, overlay)
        : Math.round(Math.max(72, Math.min(cap, room)));
      setPlace(old => old.mode === mode && old.max === max ? old : { mode, max });
    };
    update();
    window.addEventListener('resize', update); window.addEventListener('scroll', update, true);
    return () => { window.removeEventListener('resize', update); window.removeEventListener('scroll', update, true); };
  }, []);
  useEffect(() => {
    const container = list.current, option = container?.querySelectorAll<HTMLElement>('[role="option"]')[selected];
    if (!container || !option) return;
    const box = container.getBoundingClientRect(), item = option.getBoundingClientRect();
    if (item.top < box.top) container.scrollTop += item.top - box.top;
    else if (item.bottom > box.bottom) container.scrollTop += item.bottom - box.bottom;
  }, [commands, selected]);
  return <div ref={menu} className={`slash-command-menu is-${place.mode}`}>
    <div className="slash-command-heading"><strong>命令 / Skills</strong><small>{commands.length} 项</small><small>仅填入，不自动发送</small></div>
    <div ref={list} id={id + '-list'} className="slash-command-list" role="listbox" aria-label="命令建议" style={{ maxHeight: `min(${place.max}px, 48dvh)` }}>
      {commands.map((command, index) => <button key={command.prefix + command.name} id={`${id}-option-${index}`}
        type="button" role="option" aria-selected={selected === index} tabIndex={-1}
        onPointerDown={event => event.preventDefault()} onMouseDown={event => event.preventDefault()}
        onPointerMove={() => onSelect(index)} onClick={() => onPick(command)}>
        <code translate="no">{command.prefix}{command.name}</code>
        {command.description && <span translate="no">{command.description}</span>}
      </button>)}
    </div>
    {!commands.length && <p role="status">{refreshing ? '正在读取命令…' : hasCommands ? '没有匹配的命令。' : '暂无命令，可用下方刷新按钮读取。'}</p>}
  </div>;
}
