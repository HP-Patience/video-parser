import { useEffect, useRef, useState } from 'react';
import { formatBytes, formatRate, formatEta } from '../lib/format';
import type { TaskInfo } from '../lib/jobs';
import './progress.css';

type Props = { progress: NonNullable<TaskInfo['progress']> };
export default function ProgressBar({ progress }: Props) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [progress.sampled_at]);
  const bytes = Math.max(0, progress.bytes || 0);
  const total = progress.total_bytes || 0;
  const known = total > 0 && !progress.estimated_total;
  const raw = known ? Math.min(100, Math.floor(bytes / total * 100)) : undefined;
  // 进度只能往前走：重连、重新取块、取值抖动都可能让百分比往回跳一格，
  // 那会让人以为文件坏了。这里锁住已到达的最高值，换任务是 total 变化时重置。
  const seen = useRef({ total: 0, percent: 0 });
  if (raw !== undefined) {
    if (seen.current.total !== total) seen.current = { total, percent: 0 };
    if (raw > seen.current.percent) seen.current.percent = raw;
  }
  const percent = raw === undefined ? undefined : seen.current.percent;
  const fresh = !!progress.sampled_at && Math.abs(now / 1000 - progress.sampled_at) < 4;
  const speed = fresh ? progress.speed_bytes_per_second : undefined;
  const eta = speed && speed > 0 && total > bytes ? progress.eta_seconds : undefined;
  const track = progress.track || 1;
  // Single meta line: unknown values are simply omitted instead of spelled out.
  const multiTrack = (progress.tracks || 1) > 1;
  const label = multiTrack ? `轨道 ${track}/${progress.tracks}` : '下载';
  const parts = [
    percent !== undefined ? `${percent}%` : '',
    formatBytes(bytes) + (total > 0 ? ` / ${progress.estimated_total ? '约 ' : ''}${formatBytes(total)}` : ''),
    speed && speed > 0 ? formatRate(speed) : '',
    eta !== undefined && eta >= 0 ? `剩余 ${formatEta(eta)}` : '',
  ].filter(Boolean);
  return <div className="progress-block">
    <div className="progress-bar" role="progressbar" aria-label={`${label}进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
      <div className="progress-fill" style={percent !== undefined ? { width: `${percent}%` } : undefined} />
    </div>
    <div className="progress-meta"><span>{multiTrack ? label + ' · ' : ''}{parts.join(' · ')}</span></div>
  </div>;
}
