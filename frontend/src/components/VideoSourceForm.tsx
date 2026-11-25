import type { RefObject } from 'react';
import Icon from './Icon';

type Props = {
  value: string; parsing: boolean; importingLocal: boolean; inputRef: RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void; onParse: () => void; onClear: () => void; onImportLocal: () => void;
};

export default function VideoSourceForm({ value, parsing, importingLocal, inputRef, onChange, onParse, onClear, onImportLocal }: Props) {
  return <form onSubmit={event => { event.preventDefault(); onParse(); }}>
    <label id="source-label" htmlFor="source" className="sr-only">视频链接或分享文本</label>
    <div className="source-row">
      <button className="source-import-button" type="button" aria-label="选择本机视频" title="选择本机视频"
        aria-busy={importingLocal} disabled={parsing || importingLocal} onClick={onImportLocal}>
        <Icon name="link-2" />
        <span className="source-local-marker" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" focusable="false"><path d="M5 19 19 5M9 5h10v10" /></svg>
          <span translate="no">local</span>
        </span>
      </button>
      <textarea ref={inputRef} id="source" value={value} maxLength={8192} rows={1}
        placeholder="粘贴视频链接，或带链接的分享文本…" onChange={event => onChange(event.target.value)}
        onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); onParse(); } }} />
      {value && <button className="clear-button" type="button" aria-label="清空链接" onClick={onClear}><Icon name="x" /></button>}
      <button className="primary parse-button" type="submit" aria-busy={parsing} disabled={!value.trim() || parsing || importingLocal}>
        {parsing ? <><Icon name="loader-circle" className="spinner" />解析中…</> : <><Icon name="link-2" />解析视频</>}
      </button>
    </div>
  </form>;
}
