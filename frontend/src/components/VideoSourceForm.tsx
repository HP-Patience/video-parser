import type { RefObject } from 'react';
import Icon from './Icon';

type Props = {
  value: string; parsing: boolean; importingLocal: boolean; inputRef: RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void; onParse: () => void; onClear: () => void;
};

export default function VideoSourceForm({ value, parsing, importingLocal, inputRef, onChange, onParse, onClear }: Props) {
  return <form onSubmit={event => { event.preventDefault(); onParse(); }}>
    <label id="source-label" htmlFor="source" className="sr-only">视频链接或分享文本</label>
    <div className="source-row">
      <Icon name="link-2" />
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
