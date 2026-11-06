import ProgressiveImage from '../../components/ProgressiveImage';
import { attachmentUrl } from './pastedImages';
import type { StoredImage } from './pastedImages';
import './image-tray.css';

type Props = {
  projectId: string; images: StoredImage[]; onRemove?: (name: string) => void; disabled?: boolean; label?: string;
};

/** Thumbnails for images already stored beside the project. */
export default function ImageTray({ projectId, images, onRemove, disabled, label = '已添加的图片' }: Props) {
  if (!images.length) return null;
  return <ul className="image-tray" aria-label={label}>
    {images.map(image => <li key={image.name}>
      <ProgressiveImage src={attachmentUrl(projectId, image.name)} alt="" loading="lazy" decoding="async" />
      {onRemove && <button type="button" disabled={disabled} aria-label="移除这张图片"
        onClick={() => onRemove(image.name)}>×</button>}
    </li>)}
  </ul>;
}
