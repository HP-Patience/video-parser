import type { ReactNode } from 'react';

export default function WorkspaceStart({ children }: { children: ReactNode }) {
  return <section className="workspace-start" aria-labelledby="workspace-start-title">
    <div className="workspace-start-intro">
      <h2 id="workspace-start-title">从一个链接开始</h2>
    </div>
    {children}
  </section>;
}
