import type { ReactNode, RefObject } from 'react';
import Icon from './Icon';
import ThemeMorphIcon from './ThemeMorphIcon';
import { useTheme } from '../lib/theme';
import { pageIcons, pageNames, type Page } from '../features/navigation/navigation';

type Props = {
  page: Page; mobile: boolean; menuOpen: boolean; activeCount: number | null;
  sidebarRef: RefObject<HTMLElement | null>;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
  onNavigate: (page: Page, focus?: boolean) => void;
  onOpenMenu: () => void; onCloseMenu: () => void;
  children: ReactNode; overlays: ReactNode;
};

// Presentation only. Page and task state stay with their existing owners.
export default function WorkbenchLayout({ page, mobile, menuOpen, activeCount,
  sidebarRef, menuButtonRef, onNavigate, onOpenMenu, onCloseMenu,
  children, overlays }: Props) {
  const { theme, toggleTheme } = useTheme();
  const running = activeCount ?? 0;
  return <div className="modern-app">
    {menuOpen && <button className="nav-scrim" aria-label="关闭导航菜单" onClick={onCloseMenu} />}
    <aside ref={sidebarRef} className={'app-sidebar' + (menuOpen ? ' open' : '')}
      aria-label="主导航" inert={mobile && !menuOpen}
      role={menuOpen ? 'dialog' : undefined} aria-modal={menuOpen || undefined}>
      <div className="brand"><span className="brand-mark"><Icon name="clapperboard" /></span><strong>Video Parser</strong></div>
      <nav>{(Object.keys(pageNames) as Page[]).filter(name => name !== 'settings' && name !== 'transcription').map(name => <button key={name}
        className="nav-button" type="button" aria-current={page === name ? 'page' : undefined}
        onClick={() => onNavigate(name, mobile)}><Icon name={pageIcons[name]} />{pageNames[name]}
        {name === 'tasks' && running > 0 && <b className="nav-count">{running}<span className="sr-only"> 项进行中</span></b>}</button>)}</nav>
      <nav className="sidebar-settings" aria-label="应用与项目"><a className="nav-button" href="https://github.com/HP-Patience/video-parser" target="_blank" rel="noopener noreferrer" title="打开 GitHub 项目页面"><Icon name="github" />GitHub</a><button className="nav-button" type="button" title="设置"
        aria-current={page === 'settings' ? 'page' : undefined} onClick={() => onNavigate('settings', mobile)}><Icon name="settings" />设置</button></nav>
    </aside>
    <div className="app-shell" inert={menuOpen}>
      <header className="app-topbar">
        <button ref={menuButtonRef} className="mobile-menu" type="button" aria-label="打开导航菜单"
          aria-expanded={menuOpen} onClick={onOpenMenu}><Icon name="menu" /></button>
        <h1 className="topbar-title" id={'page-title-' + page} tabIndex={-1}>{pageNames[page]}</h1>
        <div className="top-actions">
          {/* Desktop shows the count in the sidebar; the topbar badge only covers the collapsed mobile nav. */}
          {mobile && running > 0 && <button className="global-task-badge" type="button"
            aria-label={`运行任务 ${running} 项，打开任务中心`}
            onClick={() => onNavigate('tasks')}><span className="status-dot" /><b>{running}</b></button>}
          <button className="theme-toggle" type="button" onClick={toggleTheme}
            aria-label={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
            title={theme === 'dark' ? '浅色主题' : '深色主题'}><ThemeMorphIcon theme={theme} /></button>
        </div>
      </header>
      <main className="workspace modern-main" aria-labelledby={'page-title-' + page}>{children}</main>
    </div>
    {overlays}
  </div>;
}
