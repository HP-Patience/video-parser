import { useEffect, useState } from 'react';
import ProgressBar from '../../components/ProgressBar';
import { request } from '../../lib/api';

type Progress = {
  state: 'idle' | 'preparing' | 'installing' | 'downloading' | 'verifying' | 'checking_cuda' | 'ready' | 'failed';
  supported: boolean;
  ready: boolean;
  downloaded_bytes: number;
  total_bytes: number | null;
  error: string | null;
};

const stages: Partial<Record<Progress['state'], string>> = {
  preparing: '正在准备转录安装…',
  installing: '正在安装转录环境…',
  downloading: '正在下载转录模型…',
  verifying: '正在校验转录模型…',
  checking_cuda: '正在检查 CUDA 转录…',
};
const statusPath = '/transcriptions/install';
const signalFor = (controller: AbortController) => AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]);

export default function TranscriptionInstall({ visible, ready, onReady }: {
  visible: boolean; ready: boolean; onReady: (ready: boolean) => void;
}) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const stage = progress && stages[progress.state];
  useEffect(() => {
    if (!visible || starting) return;
    const controller = new AbortController();
    let timer: number;
    let attached = false;
    async function poll() {
      let delay = 1200;
      try {
        const next = await request<Progress>(statusPath, signalFor(controller));
        if (controller.signal.aborted) return;
        attached = typeof next.ready === 'boolean';
        if (attached) {
          setProgress(next);
          if (!stages[next.state]) delay = 4000;
        }
      } catch {
        // Older backends do not expose the installer; leave the base readiness status visible.
      }
      if (attached && !controller.signal.aborted) timer = window.setTimeout(() => { void poll(); }, delay);
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [visible, starting]);
  const state = progress?.state;
  const installed = progress?.ready;
  useEffect(() => {
    if (installed !== undefined) onReady(installed);
  }, [installed, onReady]);
  async function install() {
    setStarting(true); setError('');
    try {
      setProgress(await request<Progress>(statusPath, AbortSignal.timeout(10000), { confirm: true }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法启动转录安装。');
    } finally { setStarting(false); }
  }
  const usable = installed ?? ready;
  const failure = !usable && state === 'failed' ? progress?.error || '安装转录组件失败。' : '';
  return <dd>
    <span aria-live="polite">{stage || (usable ? '就绪' : '未就绪')}</span>
    {state === 'downloading' && progress && <ProgressBar progress={{ bytes: progress.downloaded_bytes, total_bytes: progress.total_bytes ?? undefined }} />}
    {!usable && progress?.supported && !stage && <div className="settings-actions transcription-install-actions">
      <button type="button" disabled={starting} onClick={() => { void install(); }}>{failure ? '重试安装' : '安装转录组件'}</button>
    </div>}
    {(error || failure) && <p className="error-text" role="alert">{error || failure}</p>}
  </dd>;
}
