import { useEffect, useRef } from 'react';
import type { AnalysisFrame } from '../lib/api';
import ProgressiveImage from './ProgressiveImage';
export default function FrameLightbox({ frame, onClose, onSeek }: { frame: AnalysisFrame | null; onClose: () => void; onSeek?: (seconds: number) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (frame && !ref.current?.open) ref.current?.showModal(); else if (!frame) ref.current?.close(); }, [frame]);
  return <dialog ref={ref} className="frame-lightbox" aria-label="画面大图" onCancel={onClose} onClose={onClose} style={{ maxWidth: '94vw', maxHeight: '94vh', padding: 16 }}>
    {frame && <><div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 12 }}><strong>画面 {frame.label}</strong><button type="button" onClick={onClose} autoFocus>关闭大图</button></div>
      <ProgressiveImage src={frame.url} alt={`视频画面 ${frame.label}`} decoding="async" style={{ display: 'block', maxWidth: '88vw', maxHeight: '75vh', objectFit: 'contain' }} />
      {onSeek && <button type="button" onClick={() => { onSeek(frame.time); onClose(); }}>跳到 {frame.label}</button>}</>}
  </dialog>;
}
