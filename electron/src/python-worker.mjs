// Child-process manager for python/worker.py.
// Pure Node.js: no Electron imports, so plain `node --test` can cover it.
// The Electron main process (main.js) wires this to IPC.

import { spawn } from 'node:child_process';
import { buildStopControlLine, createLineParser, isValidWorkerMessage } from './worker-protocol.mjs';

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
      env,
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
      const record = { cancelled: false, killTimer: null, stopRequested: false };
      let child;
      try {
        child = spawnFn(executable, argv, { stdio: ['pipe', 'pipe', 'pipe'], ...(env ? { env } : {}) });
      } catch (err) {
        if (typeof onExit === 'function') {
          onExit({ code: null, signal: null, cancelled: false, spawnError: err });
        }
        return { started: false, reason: 'spawn-throw', error: err };
      }
      record.child = child;
      if (child.stdin && typeof child.stdin.on === 'function') {
        // A late write to an exited child raises EPIPE here; it must
        // never crash the main process. Delivery failures surface
        // through requestStop()'s result instead.
        child.stdin.on('error', () => {});
      }
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

    /**
     * Ask the active worker to stop cooperatively via its stdin control
     * channel. Sends one `{"type":"stop","version":1}` line and returns
     * whether the request was sent — never whether the run finished;
     * completion arrives through onExit/onMessage as usual. Unlike
     * cancel(), this never marks the record cancelled and never starts
     * the SIGTERM/SIGKILL escalation. Writes always target the record
     * captured at call time, so a stale call cannot reach a later run.
     * Reasons: 'no-active-job', 'stdin-unavailable', 'already-requested',
     * 'stop-requested', 'write-failed'.
     */
    requestStop() {
      const record = active;
      if (!record || !record.child) {
        return { requested: false, reason: 'no-active-job' };
      }
      const stdin = record.child.stdin;
      if (!stdin || typeof stdin.write !== 'function' || stdin.destroyed || stdin.writable === false) {
        return { requested: false, reason: 'stdin-unavailable' };
      }
      if (record.stopRequested) {
        return { requested: true, reason: 'already-requested' };
      }
      try {
        stdin.write(buildStopControlLine());
      } catch {
        return { requested: false, reason: 'write-failed' };
      }
      record.stopRequested = true;
      return { requested: true, reason: 'stop-requested' };
    },

    /** For tests: the active child process, if any. */
    _activeChild() {
      return active ? active.child : null;
    },
  };
}

export { createWorkerManager };
