// Worker protocol helpers shared by the Electron main process.
// Pure Node.js: no Electron imports, so plain `node --test` can cover it.

import fs from 'node:fs';
import path from 'node:path';

const SUPPORTED_VIDEO_EXTENSIONS = new Set([
  '.mp4',
  '.avi',
  '.mov',
  '.mkv',
  '.webm',
]);

const MESSAGE_TYPES = new Set([
  'started',
  'progress',
  'stage_completed',
  'result',
  'error',
]);

/**
 * Incremental newline-delimited JSON parser. Chunk boundaries from the
 * child process rarely align with message boundaries, so callers feed
 * raw stdout data and receive complete messages via onMessage.
 *
 * Malformed lines never throw: they are reported via onInvalid and the
 * parser keeps going with the rest of the stream.
 */
function createLineParser({ onMessage, onInvalid } = {}) {
  let buffer = '';
  let invalidLines = 0;

  function dispatch(line) {
    if (line.trim() === '') {
      return null;
    }
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      invalidLines += 1;
      if (typeof onInvalid === 'function') {
        onInvalid(line);
      }
      return null;
    }
    if (typeof onMessage === 'function') {
      onMessage(message, line);
    }
    return message;
  }

  return {
    feed(chunk) {
      const messages = [];
      buffer += String(chunk);
      let index;
      while ((index = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        const message = dispatch(line.replace(/\r$/, ''));
        if (message !== null) {
          messages.push(message);
        }
      }
      return messages;
    },
    flush() {
      // A clean worker ends its stream with a trailing newline, so any
      // remainder here means the process died mid-message.
      if (buffer.trim() === '') {
        buffer = '';
        return [];
      }
      const rest = buffer;
      buffer = '';
      const message = dispatch(rest);
      return message === null ? [] : [message];
    },
    invalidLines() {
      return invalidLines;
    },
  };
}

/** Non-empty string with a supported video extension, or an error string. */
function validateVideoInput(videoPath) {
  if (typeof videoPath !== 'string' || videoPath.trim() === '') {
    return 'videoPath must be a non-empty string.';
  }
  const extension = path.extname(videoPath).toLowerCase();
  if (!SUPPORTED_VIDEO_EXTENSIONS.has(extension)) {
    return (
      `Unsupported video extension ${extension || '(none)'} ` +
      `(supported: ${[...SUPPORTED_VIDEO_EXTENSIONS].join(', ')}).`
    );
  }
  return null;
}

function isValidWorkerMessage(message) {
  return (
    message !== null &&
    typeof message === 'object' &&
    MESSAGE_TYPES.has(message.type)
  );
}

/** Resolve the project-root .venv interpreter (Windows and POSIX). */
function resolvePythonExecutable(repoRoot) {
  const executable =
    process.platform === 'win32'
      ? path.join(repoRoot, '.venv', 'Scripts', 'python.exe')
      : path.join(repoRoot, '.venv', 'bin', 'python');
  if (fs.existsSync(executable)) {
    return { executable, source: 'venv' };
  }
  return { executable: null, source: 'venv', missing: executable };
}

function resolveWorkerScript(repoRoot) {
  const script = path.join(repoRoot, 'python', 'worker.py');
  if (fs.existsSync(script)) {
    return { script, missing: null };
  }
  return { script: null, missing: script };
}

export {
  SUPPORTED_VIDEO_EXTENSIONS,
  MESSAGE_TYPES,
  createLineParser,
  validateVideoInput,
  isValidWorkerMessage,
  resolvePythonExecutable,
  resolveWorkerScript,
};
