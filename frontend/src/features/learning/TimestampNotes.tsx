import MathText from '../../components/MathText';
import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { Media } from '../../lib/api';
import { t } from '../../lib/i18n';
import { chapterTime } from '../analysis/chapters';
import { backupKey, clearBackup, errorText, noteId, readBackup, studyPath, studyRequest, writeBackup } from './studyApi';
import type { NoteDraft, PersonalNote } from './studyApi';
import ImageTray from './ImageTray';
import { IMAGE_LIMIT, clipboardImages, uploadClipboard } from './pastedImages';
import './study.css';

type Props = { projectId: string; media: Media; videoRef: RefObject<HTMLVideoElement | null>; onSeek: (time: number) => void };
export default function TimestampNotes({ projectId, media, videoRef, onSeek }: Props) {
  const [notes, setNotes] = useState<PersonalNote[]>([]), [draft, setDraft] = useState<NoteDraft | null>(null);
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('正在读取个人笔记…'), [error, setError] = useState('');
  const [pasting, setPasting] = useState(false), [imageError, setImageError] = useState('');
  const current = useRef<NoteDraft | null>(null), mounted = useRef(false), editor = useRef<HTMLTextAreaElement>(null);
  const inFlight = useRef<Promise<boolean> | null>(null), timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const key = backupKey(projectId, media.resource, 'notes'), path = studyPath(projectId, 'notes');
  const removeTimer = () => { if (timer.current) { clearTimeout(timer.current); timer.current = null; } };
  function stage(value: NoteDraft | null) {
    current.current = value; if (mounted.current) setDraft(value);
    if (value) {
      if (!writeBackup(key, value) && mounted.current) setError('浏览器草稿备份不可用，请等待服务器保存成功后再离开。');
    } else clearBackup(key);
  }
  async function flush(): Promise<boolean> {
    removeTimer();
    if (inFlight.current) { const ok = await inFlight.current; if (!ok) return false; return current.current?.dirty ? flush() : true; }
    const value = current.current;
    if (!value || !value.dirty) return true;
    if (!value.text.trim() && !value.images?.length) { if (mounted.current) setStatus('输入内容后自动保存；空白不会覆盖已有笔记'); return false; }
    if (mounted.current) { setSaving(true); setStatus('正在保存笔记…'); setError(''); }
    const task = (async () => {
      try {
        const saved = await studyRequest<PersonalNote>(path + '/' + value.id, { resource: media.resource,
          time: value.time, text: value.text, expected_revision: value.revision, images: value.images ?? [] });
        const latest = current.current;
        if (latest?.id === value.id) {
          const dirty = latest.text.trim() !== saved.text || latest.time !== saved.time
            || JSON.stringify(latest.images ?? []) !== JSON.stringify(saved.images ?? []);
          const updated = { ...latest, revision: saved.revision, dirty };
          current.current = updated;
          if (mounted.current) setDraft(updated);
          // Only clear the backup if it still represents this acknowledged text, not newer typing.
          const backup = readBackup<NoteDraft>(key);
          if (!dirty && backup?.id === value.id && backup.text.trim() === saved.text) clearBackup(key);
          else writeBackup(key, updated);
        }
        if (mounted.current) {
          setNotes(previous => [...previous.filter(note => note.id !== saved.id), saved].sort((a,b) => a.time-b.time));
          setStatus(current.current?.dirty ? '有新修改，等待保存…' : '笔记已保存');
        }
        return true;
      } catch (reason) { if (mounted.current) { setStatus('笔记未保存'); setError(errorText(reason)); } return false; }
      finally { if (mounted.current) setSaving(false); }
    })();
    inFlight.current = task;
    const ok = await task; inFlight.current = null;
    if (ok && current.current?.dirty) return flush();
    return ok;
  }
  const flushRef = useRef(flush); flushRef.current = flush;
  const reloadNotes = useRef<() => void>(() => undefined);
  useEffect(() => {
    mounted.current = true; const controller = new AbortController();
    const load = () => {
      setLoading(true); setError('');
      void studyRequest<{ notes: PersonalNote[] }>(path + '?resource=' + encodeURIComponent(media.resource), undefined, controller.signal)
      .then(result => {
        if (!mounted.current || controller.signal.aborted) return;
        setNotes(result.notes); setLoading(false); setStatus('');
        const local = readBackup<NoteDraft>(key);
        if (local && /^[a-f0-9]{32}$/.test(local.id) && typeof local.text === 'string' && local.text.length <= 5000
            && Array.isArray(local.images ?? []) && (local.images ?? []).length <= IMAGE_LIMIT
            && Number.isFinite(local.time) && local.time >= 0 && Number.isInteger(local.revision) && local.revision >= 0) {
          const server = result.notes.find(note => note.id === local.id);
          if (server && server.text === local.text.trim() && server.time === local.time
              && JSON.stringify(server.images ?? []) === JSON.stringify(local.images ?? [])) {
            clearBackup(key); current.current = { ...server, dirty: false }; setDraft(current.current);
          } else if (local.dirty || !server) {
            stage({ ...local, dirty: true }); setStatus('已恢复未保存草稿，请确认后保存');
            if (server && server.revision !== local.revision) setError('服务器笔记已有更新。草稿未覆盖它，可另存为新笔记。');
          }
        }
      }).catch(reason => { if (mounted.current && !controller.signal.aborted) { setLoading(false); setStatus('笔记读取失败'); setError(errorText(reason)); } });
    };
    reloadNotes.current = load; load();
    const leave = () => { void flushRef.current(); };
    const visibility = () => { if (document.visibilityState === 'hidden') leave(); };
    window.addEventListener('pagehide', leave); document.addEventListener('visibilitychange', visibility);
    return () => { removeTimer(); leave(); mounted.current = false; controller.abort(); window.removeEventListener('pagehide', leave); document.removeEventListener('visibilitychange', visibility); };
  }, [path, key, media.resource]);
  // The box is always on screen, so the first keystroke opens a draft stamped with the current time.
  function begin(text: string) {
    const video = videoRef.current;
    const at = Math.max(0, Math.min(video?.currentTime || 0, Math.max(0, (video?.duration || media.duration) - .01)));
    stage({ id: noteId(), time: at, text, revision: 0, dirty: true, images: [] });
    setError(''); setStatus(text.trim() ? '等待自动保存…' : '输入内容后自动保存');
    removeTimer(); if (text.trim()) timer.current = setTimeout(() => { void flushRef.current(); }, 700);
  }
  function changed(text: string) {
    const value = current.current;
    if (!value) { begin(text); return; }
    stage({ ...value, text, dirty: true }); setStatus(text.trim() || value.images?.length ? '等待自动保存…' : '输入内容后自动保存');
    removeTimer(); if (text.trim() || value.images?.length) timer.current = setTimeout(() => { void flushRef.current(); }, 700);
  }
  // Pasting uploads straight away, so the draft only ever holds short file names.
  async function pasteImages(files: File[]) {
    if (!files.length || pasting) return;
    if (!current.current) begin('');
    const base = current.current;
    if (!base) return;
    const room = IMAGE_LIMIT - (base.images?.length ?? 0);
    if (room <= 0) { setImageError(`每条笔记最多 ${IMAGE_LIMIT} 张图片。`); return; }
    setPasting(true); setImageError('');
    try {
      const stored = await uploadClipboard(projectId, files, room);
      if (!mounted.current) return;
      const value = current.current;
      if (!value) return;
      stage({ ...value, images: [...(value.images ?? []), ...stored].slice(0, IMAGE_LIMIT), dirty: true });
      setStatus('等待自动保存…');
      removeTimer(); timer.current = setTimeout(() => { void flushRef.current(); }, 700);
    } catch (reason) {
      if (mounted.current) setImageError(errorText(reason));
    } finally { if (mounted.current) setPasting(false); }
  }
  function dropImage(name: string) {
    const value = current.current;
    if (!value) return;
    stage({ ...value, images: (value.images ?? []).filter(image => image.name !== name), dirty: true });
    setStatus('等待自动保存…');
    removeTimer(); timer.current = setTimeout(() => { void flushRef.current(); }, 700);
  }
  async function start(note?: PersonalNote) {
    const video = videoRef.current;
    const at = Math.max(0, Math.min(video?.currentTime || 0, Math.max(0, (video?.duration || media.duration) - .01)));
    if (!(await flush()) || !mounted.current) return;
    const target = note && current.current?.id === note.id ? current.current : note;
    stage(target ? { ...target, dirty: false } : { id: noteId(), time: at, text: '', revision: 0, dirty: true, images: [] });
    setError(''); setStatus(note ? '正在编辑；修改后自动保存' : '已记录当前时间，输入内容后自动保存');
    requestAnimationFrame(() => editor.current?.focus());
  }
  // Closing only hides the draft once it is safely on the server; a failed save keeps it in the box.
  async function closeEditor() {
    removeTimer();
    const value = current.current;
    if (!value || (!value.text.trim() && !value.images?.length)) { stage(null); setError(''); setStatus('已关闭编辑'); return; }
    if (value.dirty) {
      const ok = await flush();
      if (!ok || !mounted.current || current.current?.dirty) return;
    }
    stage(null); setError(''); setStatus('已关闭编辑');
  }
  async function remove(note: PersonalNote) {
    if (!window.confirm(t('确定删除这条个人笔记？此操作不能撤销。'))) return;
    if (!(await flush()) || !mounted.current) return;
    const latest = current.current?.id === note.id ? current.current : note;
    setSaving(true); setError('');
    try {
      await studyRequest(path + '/' + note.id + '/delete', { resource: media.resource, expected_revision: latest.revision });
      if (!mounted.current) return;
      setNotes(previous => previous.filter(item => item.id !== note.id));
      if (current.current?.id === note.id) stage(null);
      setStatus('笔记已删除');
    } catch (reason) { if (mounted.current) setError(errorText(reason)); }
    finally { if (mounted.current) setSaving(false); }
  }
  function copyAsNew() {
    if (!current.current || saving) return;
    stage({ ...current.current, id: noteId(), revision: 0, dirty: true });
    setError(''); void flush();
  }
  return <section className="timestamp-notes" aria-label="我的时间戳笔记">
    {!!notes.length && <div className="timestamp-notes-tools" aria-label="导出个人笔记">
      <a className="button" download href={`/api/workspace/projects/${projectId}/study/notes/export?resource=${encodeURIComponent(media.resource)}&format=md`}>导出笔记</a>
      <a className="button" download href={`/api/workspace/projects/${projectId}/study/notes/export?resource=${encodeURIComponent(media.resource)}&format=csv`}>导出 CSV</a>
      <a className="button" download href={`/api/workspace/projects/${projectId}/study/notes/export?resource=${encodeURIComponent(media.resource)}&format=anki`}>导出 Anki</a>
    </div>}
    {(loading || draft || error) && <p className="study-save-status" role="status">{status}</p>}
    {error && <div className="study-error" role="alert"><p>{error}</p>
      {draft && <><button type="button" disabled={saving} onClick={() => { void flush(); }}>重试保存笔记</button>
        <button type="button" disabled={saving || !draft.text.trim()} onClick={copyAsNew}>另存为新笔记</button></>}
      {!draft && <button type="button" onClick={() => reloadNotes.current()}>重新载入笔记</button>}</div>}
    <ol className="timestamp-notes-list">{notes.map(note => <li key={note.id}>
      <div className="timestamp-note-meta"><button type="button" className="timestamp-note-seek" onClick={() => onSeek(note.time)} aria-label={`跳到笔记 ${chapterTime(note.time)}`}><time>{chapterTime(note.time)}</time></button>
        <button type="button" disabled={saving} onClick={() => { void start(note); }} aria-label={`编辑 ${chapterTime(note.time)} 的笔记`}>编辑</button>
        <button type="button" disabled={saving} onClick={() => { void remove(note); }} aria-label={`删除 ${chapterTime(note.time)} 的笔记`}>删除</button></div>
      {!!note.text && <p><MathText text={note.text} /></p>}
      <ImageTray projectId={projectId} images={note.images ?? []} label={`${chapterTime(note.time)} 的笔记图片`} /></li>)}</ol>
    <div className="timestamp-note-composer">
      <label htmlFor="personal-note-text">{draft ? <>{draft.revision ? '编辑笔记' : '新笔记'} · {chapterTime(draft.time)}</> : '新笔记'}</label>
      <ImageTray projectId={projectId} images={draft?.images ?? []} disabled={saving || pasting} onRemove={dropImage} />
      <textarea ref={editor} id="personal-note-text" maxLength={5000} value={draft?.text ?? ''} onChange={event => changed(event.target.value)}
        onPaste={event => { const files = clipboardImages(event); if (!files.length) return; event.preventDefault(); void pasteImages(files); }}
        placeholder="写下自己的理解、疑问或重点，也可以直接粘贴图片。" rows={4} />
      {pasting && <p className="study-save-status" role="status">正在保存图片…</p>}
      {imageError && <p className="error-text" role="alert">{imageError}</p>}
      <div className="timestamp-note-editor-actions"><small>{(draft?.text.length ?? 0)} / 5000</small>
        {draft && (!!draft.text.trim() || !!draft.images?.length) && <button type="button" disabled={saving || !draft.dirty} onClick={() => { void flush(); }}>立即保存笔记</button>}
        {draft && <button type="button" disabled={saving} onClick={() => { void closeEditor(); }}>关闭编辑</button>}
        <button type="button" disabled={loading || saving} onClick={() => { void start(); }}>记下当前时刻</button></div>
    </div>
  </section>;
}
