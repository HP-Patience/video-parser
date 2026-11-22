import { useEffect, useRef, useState } from 'react';
import Icon from '../../components/Icon';
import { request } from '../../lib/api';
import type { VideoProject } from '../../lib/api';
import './project-panel.css';

type Props = { project: VideoProject };

/* Titles are renamed by double-clicking them in place (components/ProjectTitle), so this
   panel only owns the archive path and the "open folder" action. */
export default function ProjectPanel({ project }: Props) {
  const [message, setMessage] = useState('');
  const [opening, setOpening] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); }, []);
  async function open() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setOpening(true);
    try {
      const result = await request<{ message: string }>(`/projects/${project.id}/open`, controller.signal, {});
      if (!controller.signal.aborted) setMessage(result.message);
    } catch (error) {
      if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : '无法打开项目目录。');
    } finally {
      if (!controller.signal.aborted) { active.current = null; setOpening(false); }
    }
  }
  return <div className="project-panel">
    <div className="project-actions">
      <code className="project-path" title={`outputs/${project.location}`}>outputs/{project.location}</code>
      <button type="button" aria-label="打开项目文件夹" title="打开项目文件夹" disabled={opening} aria-busy={opening} onClick={() => { void open(); }}><Icon name="external-link" />打开</button>
    </div>
    {message && <p className="project-message" aria-live="polite">{message}</p>}
  </div>;
}
