import type { RefObject } from 'react';
import type { Media } from '../lib/api';
import { phases } from '../lib/jobs';
import type { TaskInfo } from '../lib/jobs';
import Icon from './Icon';
import ProgressBar from './ProgressBar';
import VideoControls from './VideoControls';

type Props = {
  projectId?: string; title?: string; coverURL?: string | null; media: Media | null;
  videoRef: RefObject<HTMLVideoElement | null>; coverError: boolean;
  onCoverError: () => void; onVideoError: () => void;
  preparing: boolean; failed: boolean; message: string; task?: TaskInfo;
  cancelDisabled: boolean; retryDisabled: boolean; onCancel: () => void; onRetry: () => void;
};
export default function MediaStage({ projectId, title, coverURL, media, videoRef, coverError, onCoverError,
  onVideoError, preparing, failed, message, task, cancelDisabled, retryDisabled, onCancel, onRetry }: Props) {
  const state = media ? 'player' : preparing ? 'progress' : failed ? 'failure' : 'cover';
  const cancelled = task?.state === 'cancelled';
  return <div className={`media-stage unified-media-stage${failed && !media ? ' media-stage-failure' : ''}`} id="video-stage" data-state={state}>
    {media ? <video ref={videoRef} key={`${projectId}:${media.preview_url}`} playsInline preload="metadata" onClick={event => {
      const video = event.currentTarget;
      if (video.paused) void video.play().catch(() => undefined); else video.pause();
    }} onKeyDownCapture={event => {
      if (event.altKey || event.ctrlKey || event.metaKey || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const video = event.currentTarget;
      video.currentTime = Math.max(0, Math.min(Number.isFinite(video.duration) ? video.duration : Infinity, video.currentTime + (event.key === 'ArrowRight' ? 5 : -5)));
    }}
      poster={coverError ? undefined : coverURL || undefined} src={media.preview_url} onError={onVideoError} aria-label="视频播放器" />
      : preparing || failed ? <div className="empty-stage download-stage"><div className="download-preview-feedback">
        <div className="download-preview-heading"><Icon name={preparing ? 'loader-circle' : cancelled ? 'x' : 'circle-play'} className={preparing ? 'spinner' : ''} />
          <span>{preparing ? '视频下载与归档' : cancelled ? '下载已取消' : task?.state === 'interrupted' ? '下载已中断' : '下载或归档未完成'}</span></div>
        {preparing && <p className="media-phase">{task?.state === 'cancelling' ? '正在取消' : phases[task?.phase || 'starting'] || '处理中'}</p>}
        <p className="download-preview-message" role={failed ? 'alert' : 'status'} aria-live="polite" aria-atomic="true">{message}</p>
        {preparing && task?.phase === 'downloading' && task.progress && <ProgressBar progress={task.progress} />}
        <div className="media-feedback-actions">{preparing ? <button type="button" disabled={cancelDisabled} onClick={onCancel}>
          {task?.state === 'cancelling' ? '正在取消…' : '取消下载'}</button> : <button type="button" className="primary" disabled={retryDisabled} onClick={onRetry}>重新下载并归档</button>}</div>
      </div></div> : coverURL && !coverError ? <img src={coverURL} alt={`${title || '视频'}的封面`} onError={onCoverError} />
      : <div className="empty-stage"><Icon name="image" className="empty-glyph" /><p>{projectId ? '暂无可用封面，选择画质即可下载归档' : '解析链接后在这里查看视频封面'}</p></div>}
      {media && <VideoControls videoRef={videoRef} projectId={projectId} resource={media.resource} mediaKey={`${projectId}:${media.preview_url}`} />}
  </div>;
}
