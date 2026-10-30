// Child-process manager for python/worker.py.
// Pure Node.js: no Electron imports, so plain `node --test` can cover it.
// The Electron main process (main.js) wires this to IPC.

import { spawn } from 'node:child_process';
import { createLineParser, isValidWorkerMessage } from './worker-protocol.mjs';

function createWorkerManager({ spawnFn = spawn } = {}) {
  let active = null;

  function clearIfCurrent(record) {
    if (active === record) {
      active = null;
    }
  }

  return {
    isBusy() {
      return active !== null;
    },

    /**
     * Launch the worker. At most one job runs at a time; a second start
     * while busy returns { started: false, reason: 'busy' } and never
     * spawns. Callbacks: onMessage(message), onStderr(text),
     * onExit({ code, signal, cancelled, spawnError }).
     */
    start({
      executable,
      argv = [],
      onMessage,
      onStderr,
      onExit,
    } = {}) {
      if (active !== null) {
        return { started: false, reason: 'busy' };
      }
      if (typeof executable !== 'string' || executable === '') {
        throw new TypeError('executable must be a non-empty string.');
      }
      const record = { cancelled: false, killTimer: null };
      let child;
      try {
        child = spawnFn(executable, argv, { stdio: ['ignore', 'pipe', 'pipe'] });
      } catch (err) {
        if (typeof onExit === 'function') {
          onExit({ code: null, signal: null, cancelled: false, spawnError: err });
        }
        return { started: false, reason: 'spawn-throw', error: err };
      }
      record.child = child;
      active = record;

      const parser = createLineParser({
        onInvalid: (line) => {
          if (typeof onStderr === 'function') {
            onStderr(`[worker stdout] ignored non-JSON line: ${line.slice(0, 200)}`);
          }
        },
      });
      const forward = (message) => {
        if (typeof onMessage === 'function') {
          if (isValidWorkerMessage(message)) {
            onMessage(message);
          } else {
            onMessage({
              type: 'error',
              stage: 'protocol',
              message: `Unknown worker message type: ${JSON.stringify(message && message.type)}`,
            });
          }
        }
      };

      child.stdout.on('data', (chunk) => {
        for (const message of parser.feed(chunk)) {
          forward(message);
        }
      });
      if (child.stderr) {
        child.stderr.on('data', (chunk) => {
          if (typeof onStderr === 'function') {
            onStderr(String(chunk));
          }
        });
      }
      const finish = (outcome) => {
        if (record.killTimer) {
          clearTimeout(record.killTimer);
          record.killTimer = null;
        }
        clearIfCurrent(record);
        if (typeof onExit === 'function') {
          onExit(outcome);
        }
      };
      child.on('error', (err) => {
        // Spawn failure (missing interpreter, permission). No 'close'
        // follows on some platforms, so settle here.
        finish({ code: null, signal: null, cancelled: record.cancelled, spawnError: err });
      });
      child.on('close', (code, signal) => {
        for (const message of parser.flush()) {
          forward(message);
        }
        finish({ code, signal, cancelled: record.cancelled, spawnError: null });
      });
      return { started: true, pid: child.pid ?? null };
    },

    /**
     * Terminate the active job. Resolves true once the process has exited
     * (or when there was nothing running). SIGTERM first, SIGKILL after
     * killGraceMs on POSIX; plain kill() on Windows.
     */
    cancel({ killGraceMs = 3000 } = {}) {
      const record = active;
      if (!record || !record.child) {
        return Promise.resolve(false);
      }
      record.cancelled = true;
      return new Promise((resolve) => {
        const done = () => resolve(true);
        try {
          record.child.once('close', done);
        } catch {
          resolve(true);
          return;
        }
        try {
          record.child.kill('SIGTERM');
        } catch {
          // Already gone; 'close' will still settle the promise.
        }
        if (process.platform !== 'win32') {
          record.killTimer = setTimeout(() => {
            try {
              record.child.kill('SIGKILL');
            } catch {
              // Process already exited.
            }
          }, killGraceMs);
          if (typeof record.killTimer.unref === 'function') {
            record.killTimer.unref();
          }
        }
      });
    },

    /** For tests: the active child process, if any. */
    _activeChild() {
      return active ? active.child : null;
    },
  };
}

export { createWorkerManager };
