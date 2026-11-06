import type { ReactNode, RefObject } from 'react';
import Icon from './Icon';
import ThemeMorphIcon from './ThemeMorphIcon';
import { useTheme } from '../lib/theme';
import { navigationGroups, pageIcons, pageNames, type Page } from '../features/navigation/navigation';

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
  const navButton = (name: Page) => <button key={name}
    className="nav-button" type="button" aria-current={page === name ? 'page' : undefined}
    onClick={() => onNavigate(name, mobile)}><Icon name={pageIcons[name]} />{pageNames[name]}
    {name === 'tasks' && running > 0 && <b className="nav-count">{running}<span className="sr-only"> 项进行中</span></b>}</button>;
  return <div className="modern-app">
    {menuOpen && <button className="nav-scrim" aria-label="关闭导航菜单" onClick={onCloseMenu} />}
    <aside ref={sidebarRef} className={'app-sidebar' + (menuOpen ? ' open' : '')}
      aria-label="主导航" inert={mobile && !menuOpen}
      role={menuOpen ? 'dialog' : undefined} aria-modal={menuOpen || undefined}>
      <div className="brand"><span className="brand-mark"><Icon name="clapperboard" /></span><div className="brand-copy"><strong>Video Parser</strong><small>Local video workspace</small></div></div>
      <nav className="sidebar-nav" aria-label="工作区导航">{navigationGroups.map(group => <div className="nav-group" key={group.id}
        role="group" aria-labelledby={`nav-group-${group.id}`}>
        <p className="nav-caption" id={`nav-group-${group.id}`}>{group.label}</p>
        <div className="nav-group-items">{group.pages.map(navButton)}</div>
      </div>)}</nav>
      <nav className="sidebar-utility sidebar-settings" aria-label="账号与设置">
        {navButton('accounts')}
        {navButton('settings')}
      </nav>
    </aside>
    <div className="app-shell" inert={menuOpen}>
      <header className="app-topbar">
        <button ref={menuButtonRef} className="mobile-menu" type="button" aria-label="打开导航菜单"
          aria-expanded={menuOpen} onClick={onOpenMenu}><Icon name="menu" /></button>
        <div className="topbar-heading"><p className="topbar-kicker">Personal media workspace</p><h1 className="topbar-title" id={'page-title-' + page} tabIndex={-1}>{pageNames[page]}</h1></div>
        <div className="top-actions">
          {running > 0 && <button className="global-task-badge" type="button"
            aria-label={`运行任务 ${running} 项，打开任务中心`}
            onClick={() => onNavigate('tasks')}><span className="status-dot" /><span className="task-label">进行中</span><b>{running}</b></button>}
          <a className="topbar-github" href="https://github.com/HP-Patience/video-parser" target="_blank" rel="noopener noreferrer" aria-label="打开 GitHub 项目页面" title="GitHub 项目页面"><Icon name="github" /></a>
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
