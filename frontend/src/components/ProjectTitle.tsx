import { useEffect, useRef, useState } from 'react';
import type { VideoProject } from '../lib/api';
import { notify } from '../lib/notify';
import { renameProjectFiles } from '../lib/renameProject';
import './project-title.css';

/**
 * One title treatment for every place a video name shows up.
 *
 * Same weight, line box and truncation, plus the same affordance: double-click turns
 * the text into an input. Committing renames the archived files through the naming
 * pipeline (preview + token + transactional move), never a bare filesystem rename.
 */
type Props = {
  text: string;
  /** Heading id, for aria-labelledby on the surrounding card. */
  id?: string;
  /** Tooltip text when the visible title is shortened or differs from the full one. */
  full?: string;
  /** Enables editing: the archived files of this project get renamed. */
  projectId?: string;
  onRenamed?: (project: VideoProject) => void;
  disabled?: boolean;
  /** Supporting text inside a detail list, not a heading. */
  compact?: boolean;
  as?: 'span' | 'strong' | 'h3' | 'h4';
  className?: string;
};

export default function ProjectTitle({ text, id, full, projectId, onRenamed, disabled = false, compact = false, as: Tag = 'span', className = '' }: Props) {
  const [draft, setDraft] = useState<string | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  const cancelled = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  // A refreshed project (or a rename from elsewhere) wins over any local echo.
  useEffect(() => { setShown(null); }, [text]);
  const editable = !!projectId && !disabled;
  const value = shown || text;
  const tip = full && full !== value ? full : '';
  // While editing the field is the target, so the hover affordance steps aside.
  const classes = ['project-title', compact ? 'is-compact' : '', editable && draft === null ? 'is-editable' : '', draft !== null ? 'is-editing' : '', className]
    .filter(Boolean).join(' ');

  function begin() {
    if (!editable || draft !== null) return;
    cancelled.current = false;
    setDraft(value);
    requestAnimationFrame(() => {
      const input = field.current;
      if (!input) return;
      input.focus(); input.select();
      // Long titles scroll to the caret end; start at the head so the name reads normally.
      input.scrollLeft = 0;
    });
  }
  function stop() {
    cancelled.current = false;
    setDraft(null);
  }
  async function commit() {
    if (draft === null) return;
    const next = draft.replace(/\s+/g, ' ').trim();
    if (cancelled.current || !next || next === value || !projectId) { stop(); return; }
    if (controller.current) return;
    const abort = new AbortController();
    controller.current = abort;
    setSaving(true);
    try {
      const project = await renameProjectFiles(projectId, next, abort.signal);
      if (abort.signal.aborted) return;
      setShown(next); setDraft(null);
      onRenamed?.(project);
      notify('已重命名归档文件。');
    } catch (reason) {
      if (abort.signal.aborted) return;
      setDraft(null);
      notify(reason instanceof Error ? reason.message : '改名没有完成，原文件仍在。', 'error');
    } finally {
      if (controller.current === abort) { controller.current = null; setSaving(false); }
    }
  }
  return <Tag id={id} className={classes} translate="no" aria-busy={saving || undefined}
    title={editable ? `${tip || value}（双击改文件名）` : tip || undefined}
    onDoubleClick={editable ? begin : undefined}>
    {draft === null ? value : <input ref={field} className="project-title-field" value={draft} maxLength={100}
      aria-label="新的文件名" spellCheck={false} disabled={saving}
      onChange={event => setDraft(event.target.value)}
      onKeyDown={event => {
        if (event.key === 'Enter') { event.preventDefault(); void commit(); }
        else if (event.key === 'Escape') {
          event.preventDefault();
          cancelled.current = true;
          field.current?.blur();
          stop();
        }
      }}
      onBlur={() => { void commit(); }} />}
  </Tag>;
}
