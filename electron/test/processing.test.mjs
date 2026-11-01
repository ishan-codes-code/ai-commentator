import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { registerProcessingIpc } from '../src/main/ipc/processing.js';
import { createProject, getProject, updateProject } from '../src/project-store.mjs';

// Real repo root so resolvePythonExecutable/resolveWorkerScript find the
// checked-in .venv layout and worker script; the fake manager never spawns.
const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..', '..');

let rootCounter = 0;

function makeUserData(t) {
  rootCounter += 1;
  const base = fs.mkdtempSync(
    path.join(os.tmpdir(), `ai-commentator-stop-ipc-${process.pid}-${rootCounter}-`),
  );
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  return base;
}

function makeFixtureVideo(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, 'clip.mp4');
  fs.writeFileSync(file, Buffer.alloc(1024, 7));
  return file;
}

function setup(t, { requestStopResult } = {}) {
  const userData = makeUserData(t);
  const handlers = {};
  const ipcMain = {
    handle: (name, fn) => {
      handlers[name] = fn;
    },
  };
  const app = { getPath: (name) => {
    assert.equal(name, 'userData');
    return userData;
  } };
  const dialog = { showOpenDialog: async () => ({ canceled: true, filePaths: [] }) };
  const fakeFs = { statSync: () => ({ size: 8 }) };
  let captured = null;
  const workerManager = {
    isBusy: () => false,
    start: (calls) => {
      captured = calls;
      return { started: true, pid: 1 };
    },
    // Explicit undefined check: `??` would swallow an intentionally null
    // result that the malformed-result test needs to exercise.
    requestStop: () => (requestStopResult === undefined
      ? { requested: false, reason: 'no-active-job' }
      : requestStopResult),
    cancel: async () => false,
  };
  const state = { activeProjectId: null, appQuitting: false };
  const events = [];
  registerProcessingIpc({
    ipcMain,
    app,
    dialog,
    fs: fakeFs,
    workerManager,
    state,
    repoRoot: () => repoRoot,
    sendWorkerEvent: (message) => events.push(message),
    getMainWindow: () => null,
  });
  return { userData, handlers, workerManager, state, events, getStart: () => captured };
}

async function startRun(t, setupResult, videoPath) {
  const video = videoPath ?? makeFixtureVideo(path.join(setupResult.userData, 'incoming'));
  const project = createProject(setupResult.userData, { name: 'stop test', sourcePath: video });
  // ipcMain passes the event first; skipVision keeps the test independent
  // of any ambient BASETEN_API_KEY.
  const started = await setupResult.handlers['process-video'](
    {}, video, { skipVision: true, projectId: project.id },
  );
  assert.equal(started.ok, true);
  // Mirror the renderer: a started run marks its project processing, which
  // is the state the terminal onExit settlement settles from.
  updateProject(setupResult.userData, project.id, { processingStatus: 'processing' });
  return project.id;
}

test('request-stop reports no active job', async (t) => {
  const s = setup(t);
  assert.deepEqual(await s.handlers['request-stop'](), {
    ok: true,
    requested: false,
    reason: 'no-active-job',
  });
});

test('request-stop forwards an accepted request', async (t) => {
  const s = setup(t, { requestStopResult: { requested: true, reason: 'stop-requested' } });
  assert.deepEqual(await s.handlers['request-stop'](), {
    ok: true,
    requested: true,
    reason: 'stop-requested',
  });
});

test('request-stop reports undeliverable requests without internals', async (t) => {
  const s = setup(t, { requestStopResult: { requested: false, reason: 'stdin-unavailable' } });
  assert.deepEqual(await s.handlers['request-stop'](), {
    ok: true,
    requested: false,
    reason: 'stdin-unavailable',
  });
});

test('request-stop survives a throwing manager', async (t) => {
  const s = setup(t);
  s.workerManager.requestStop = () => {
    throw new Error('boom');
  };
  assert.deepEqual(await s.handlers['request-stop'](), {
    ok: false,
    error: 'Stop request failed.',
  });
});

test('request-stop rejects malformed manager results', async (t) => {
  const s = setup(t, { requestStopResult: null });
  assert.deepEqual(await s.handlers['request-stop'](), {
    ok: false,
    error: 'Stop request failed.',
  });
});

test('a stopped run settles the project as stopped', async (t) => {
  const s = setup(t);
  const projectId = await startRun(t, s);
  const start = s.getStart();
  start.onMessage({ type: 'started', message: 'Processing video clip.mp4' });
  start.onMessage({ type: 'stopped', data: { status: 'stopped' } });
  start.onExit({ code: 0, signal: null, cancelled: false });
  assert.equal(getProject(s.userData, projectId).processingStatus, 'stopped');
  assert.deepEqual(
    s.events.filter((m) => m.type === 'stopped'),
    [{ type: 'stopped', data: { status: 'stopped' } }],
  );
});

test('a completed run still settles as done', async (t) => {
  const s = setup(t);
  const projectId = await startRun(t, s);
  const start = s.getStart();
  start.onMessage({ type: 'result', data: {} });
  start.onExit({ code: 0, signal: null, cancelled: false });
  assert.equal(getProject(s.userData, projectId).processingStatus, 'done');
});

test('a cancelled run still settles as cancelled', async (t) => {
  const s = setup(t);
  const projectId = await startRun(t, s);
  const start = s.getStart();
  start.onExit({ code: null, signal: 'SIGTERM', cancelled: true });
  assert.equal(getProject(s.userData, projectId).processingStatus, 'cancelled');
});

test('a stopped flag does not leak into the next run', async (t) => {
  const s = setup(t);
  const firstId = await startRun(t, s);
  const first = s.getStart();
  first.onMessage({ type: 'stopped', data: { status: 'stopped' } });
  first.onExit({ code: 0, signal: null, cancelled: false });
  assert.equal(getProject(s.userData, firstId).processingStatus, 'stopped');
  const secondId = await startRun(t, s);
  const second = s.getStart();
  second.onExit({ code: 0, signal: null, cancelled: false });
  assert.equal(getProject(s.userData, secondId).processingStatus, 'done');
});

test('quitting still settles as interrupted after a stop', async (t) => {
  const s = setup(t);
  const projectId = await startRun(t, s);
  const start = s.getStart();
  start.onMessage({ type: 'stopped', data: { status: 'stopped' } });
  s.state.appQuitting = true;
  start.onExit({ code: 0, signal: null, cancelled: false });
  assert.equal(getProject(s.userData, projectId).processingStatus, 'interrupted');
});
