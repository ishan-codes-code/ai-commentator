/**
 * Small pure formatting helpers for renderer display.
 * No IPC, no DOM, no side effects.
 */

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export function formatBytes(sizeBytes) {
  if (typeof sizeBytes !== 'number' || !Number.isFinite(sizeBytes) || sizeBytes < 0) {
    return '—';
  }
  if (sizeBytes === 0) {
    return '0 B';
  }
  const index = Math.min(
    Math.floor(Math.log(sizeBytes) / Math.log(1024)),
    UNITS.length - 1,
  );
  const value = sizeBytes / 1024 ** index;
  const rounded = value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${UNITS[index]}`;
}

export function fileNameOf(filePath) {
  if (typeof filePath !== 'string' || filePath === '') {
    return '';
  }
  return filePath.split(/[/\\]/).pop() ?? filePath;
}

export function extensionOf(filePath) {
  const name = fileNameOf(filePath);
  const dot = name.lastIndexOf('.');
  if (dot <= 0) {
    return '';
  }
  return name.slice(dot + 1).toLowerCase();
}

/**
 * Convert an absolute local path (from the trusted main-process file
 * dialog) into a `file://` URL for the `<video>` preview element.
 * Returns '' for anything that is not a usable absolute path.
 */
/**
 * Human relative time ("3h ago") for `lastOpenedAt` display.
 * Returns an em dash for unparseable input, never throws.
 */
export function timeAgo(isoString, nowMs = Date.now()) {
  const then = Date.parse(isoString);
  if (typeof isoString !== 'string' || Number.isNaN(then)) {
    return '—';
  }
  const seconds = Math.max(0, Math.floor((nowMs - then) / 1000));
  if (seconds < 60) {
    return 'just now';
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  const days = Math.floor(hours / 24);
  if (days < 30) {
    return `${days}d ago`;
  }
  const months = Math.floor(days / 30);
  if (months < 12) {
    return `${months}mo ago`;
  }
  return `${Math.floor(months / 12)}y ago`;
}

export function fileUrlOf(filePath) {
  if (typeof filePath !== 'string' || filePath === '') {
    return '';
  }
  const segments = filePath.split(/[\\/]/).filter((part) => part !== '');
  if (segments.length === 0) {
    return '';
  }
  const encoded = segments.map((part, index) => {
    if (index === 0 && /^[A-Za-z]:$/.test(part)) {
      return part;
    }
    return encodeURIComponent(part);
  });
  const absolute = /^[A-Za-z]:$/.test(segments[0]) || filePath.startsWith('/');
  if (!absolute) {
    return '';
  }
  return `file:///${encoded.join('/')}`;
}
