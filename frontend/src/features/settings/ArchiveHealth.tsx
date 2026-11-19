import { useState } from 'react';
import { request } from '../../lib/api';
import type { ArchiveHealth, ArchiveHealthIssue } from '../../lib/api';
import { formatBytes } from '../../lib/format';
import { workspaceUrl } from '../../lib/api-base';
import './archive-health.css';

type Listing = { items: unknown[]; total: number; shown: number };

function count(listing: Listing) {
  return listing.total > listing.shown ? `共 ${listing.total} 项，仅显示前 ${listing.shown} 项。` : '';
}

export default function ArchiveHealthPanel({ visible }: { visible: boolean }) {
  const [report, setReport] = useState<ArchiveHealth | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function scan() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      setReport(await request<ArchiveHealth>('/archive/health', new AbortController().signal));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '体检失败，请重试。');
    } finally { setBusy(false); }
  }
  const totals = report?.totals;
  return <div className="archive-health" hidden={!visible}>
    <div className="health-toolbar">
      <div>
        <p className="health-lead">归档体检只读取项目清单和文件大小，不会删除、移动或修复任何文件。</p>
        {report && <p className="settings-hint">{`归档位置：${report.archive_root} · 扫描耗时 ${report.duration_ms} 毫秒`}</p>}
      </div>
      <div className="health-actions">
        <button type="button" className="primary" disabled={busy} aria-busy={busy} onClick={() => { void scan(); }}>{busy ? '正在扫描归档…' : report ? '重新扫描' : '开始体检'}</button>
        {report && <a className="button" download href={workspaceUrl('/archive/health/export?format=md')}>导出体检报告</a>}
      </div>
    </div>
    {error && <p className="error-text" role="alert">{error}</p>}
    {!report && !error && <p className="settings-hint">还没有扫描。体检不会读取视频内容，只核对文件是否存在、大小是否与清单一致，并按记录的 SHA-256 找出重复内容。</p>}
    {report && totals && <>
      <dl className="health-totals">
        <div><dt>项目</dt><dd>{totals.projects}</dd></div>
        <div><dt>磁盘占用</dt><dd>{formatBytes(totals.disk_bytes)}</dd></div>
        <div><dt>登记文件</dt><dd>{totals.files}</dd></div>
        <div><dt>缺失文件</dt><dd>{totals.missing}</dd></div>
        <div><dt>大小变化</dt><dd>{totals.changed}</dd></div>
        <div><dt>重复组</dt><dd>{totals.duplicate_groups}</dd></div>
        <div><dt>未登记文件</dt><dd>{formatBytes(totals.orphan_bytes)}</dd></div>
      </dl>
      {!totals.projects && <p className="settings-hint">归档目录里还没有项目。</p>}
      {!!totals.projects && <>
        <section className="health-block" aria-labelledby="health-projects"><h4 id="health-projects">按项目统计</h4>
          <table className="health-table"><thead><tr><th scope="col">项目</th><th scope="col">平台</th><th scope="col">磁盘占用</th><th scope="col">文件</th><th scope="col">缺失</th><th scope="col">变化</th><th scope="col">未登记</th></tr></thead>
            <tbody>{report.projects.slice(0, 100).map(row => <tr key={row.id}>
              <th scope="row">{row.title}</th><td>{row.platform}</td><td>{formatBytes(row.disk_bytes)}</td>
              <td>{row.files}</td><td>{row.missing}</td><td>{row.changed}</td><td>{formatBytes(row.orphan_bytes)}</td></tr>)}</tbody>
          </table>
          {report.projects.length > 100 && <p className="settings-hint">{`共 ${report.projects.length} 个项目，仅显示占用最大的 100 个。`}</p>}
        </section>
        <div className="health-issues">
          <details className="health-block" open={totals.missing > 0}>
            <summary>缺失文件（记录存在，磁盘上找不到）· {report.issues.missing.total}</summary>
            <ul>{report.issues.missing.items.map(item => <IssueRow key={`${item.project_id}:${item.path}`} item={item} kind="missing" />)}</ul>
            {count(report.issues.missing) && <p className="settings-hint">{count(report.issues.missing)}</p>}
          </details>
          <details className="health-block" open={totals.changed > 0}>
            <summary>大小变化的文件（可能被截断或替换）· {report.issues.changed.total}</summary>
            <ul>{report.issues.changed.items.map(item => <IssueRow key={`${item.project_id}:${item.path}`} item={item} kind="changed" />)}</ul>
            {count(report.issues.changed) && <p className="settings-hint">{count(report.issues.changed)}</p>}
          </details>
          <details className="health-block" open={totals.duplicate_groups > 0}>
            <summary>重复内容（相同 SHA-256）· {report.issues.duplicates.total}</summary>
            <ul>{report.issues.duplicates.items.map(group => <li key={group.sha256}>
              <strong>{formatBytes(group.size_bytes)}</strong><span className="health-note">{`相同内容 ${group.copies ?? group.entries.length} 份`}</span>
              <ul className="health-duplicates">{group.entries.map(entry => <li key={`${entry.project_id}:${entry.path}`}>{entry.title}<code>{entry.path}</code></li>)}</ul>
            </li>)}</ul>
            {count(report.issues.duplicates) && <p className="settings-hint">{count(report.issues.duplicates)}</p>}
          </details>
          <details className="health-block">
            <summary>未登记文件（不在项目清单里）· {report.issues.orphans.total}</summary>
            <ul>{report.issues.orphans.items.map(item => <li key={`${item.project_id}:${item.path}`}>
              <span>{item.title}</span><code>{item.path}</code><span className="health-note">{formatBytes(item.size_bytes || 0)}</span></li>)}</ul>
            {count(report.issues.orphans) && <p className="settings-hint">{count(report.issues.orphans)}</p>}
          </details>
          {!!report.issues.unsafe.total && <details className="health-block">
            <summary>路径不安全的登记项 · {report.issues.unsafe.total}</summary>
            <ul>{report.issues.unsafe.items.map(item => <li key={`${item.project_id}:${item.path}`}><span>{item.title}</span><code>{item.path}</code><span className="health-note">{item.reason}</span></li>)}</ul>
          </details>}
          {!!report.issues.unreadable.total && <details className="health-block">
            <summary>无法读取的项目清单 · {report.issues.unreadable.total}</summary>
            <ul>{report.issues.unreadable.items.map(item => <li key={item.path}><code>{item.path}</code><span className="health-note">{item.error}</span></li>)}</ul>
          </details>}
        </div>
        {!totals.missing && !totals.changed && !totals.duplicate_groups && !totals.orphan_files && <p className="settings-hint">本次体检没有发现问题。</p>}
        <p className="settings-hint">“未登记文件”不等于垃圾：可能是手动放入的素材、旧版本或改名遗留。体检不提供一键清理，删除任何文件前请先备份。</p>
      </>}
    </>}
  </div>;
}

function IssueRow({ item, kind }: { item: ArchiveHealthIssue; kind: 'missing' | 'changed' }) {
  return <li>
    <span>{item.title}</span><code>{item.path}</code>
    <span className="health-note">{item.recorded_bytes !== undefined && item.recorded_bytes !== null ? `记录 ${formatBytes(item.recorded_bytes)}` : '大小未记录'}</span>
    {kind === 'changed' && <span className="health-note">{`实际 ${formatBytes(item.actual_bytes || 0)}`}</span>}
    {item.suspected_move && <span className="health-note">可能已移动到 <code>{item.suspected_move}</code></span>}
  </li>;
}
