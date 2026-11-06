import './lib/i18n'; // First: applies the saved interface language before React's first paint.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/global.css';

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);

import './styles/modern.css';
import './styles/design-system.css';
import './styles/ux-refinements.css';
import './styles/spacing.css';
import './styles/typography.css';
import './styles/soft-workbench.css';
