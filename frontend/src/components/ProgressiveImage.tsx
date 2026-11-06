import { useState } from 'react';
import type { ImgHTMLAttributes } from 'react';

type Props = Omit<ImgHTMLAttributes<HTMLImageElement>, 'src' | 'onLoad' | 'onError'> & {
  src: string;
  onLoad?: ImgHTMLAttributes<HTMLImageElement>['onLoad'];
  onError?: ImgHTMLAttributes<HTMLImageElement>['onError'];
};

/** Keeps image space visible and fades decoded content in instead of showing a blank frame. */
export default function ProgressiveImage({ src, className = '', onLoad, onError, ...props }: Props) {
  const [loadedSrc, setLoadedSrc] = useState('');
  const [failedSrc, setFailedSrc] = useState('');
  const state = failedSrc === src ? 'error' : loadedSrc === src ? 'loaded' : 'loading';
  return <img {...props} src={src} className={`progressive-image is-${state}${className ? ' ' + className : ''}`}
    onLoad={event => { setLoadedSrc(src); onLoad?.(event); }}
    onError={event => { setFailedSrc(src); onError?.(event); }} />;
}
