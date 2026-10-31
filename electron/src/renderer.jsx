import { createRoot } from 'react-dom/client';
import { MotionConfig } from 'motion/react';
import App from '@/app/App.jsx';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/geist-mono/400.css';
import '@fontsource/geist-mono/500.css';
import './index.css';

const container = document.getElementById("root");
const root = createRoot(container);
root.render(
  <MotionConfig reducedMotion="user">
    <App />
  </MotionConfig>,
);
