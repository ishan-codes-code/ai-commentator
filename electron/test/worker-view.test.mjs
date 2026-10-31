import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MAX_WORKER_LOGS,
  STAGE_LABELS,
  applyWorkerMessage,
  createInitialRunState,
  stageLabel,
} from '../src/lib/worker-view.js';
import { extensionOf, fileNameOf, fileUrlOf, formatBytes, projectMediaUrl, timeAgo } from '../src/lib/format.js';

test('stageLabel maps known stages and passes unknown through', () => {
  assert.equal(stageLabel('frame_extraction'), 'Frame extraction');
  assert.equal(stageLabel('vision_analysis'), 'Vision analysis');
  assert.equal(stageLabel('state_rebuild'), 'Match-state reconstruction');
  assert.equal(stageLabel('event_detection'), 'Event detection');
  assert.equal(stageLabel('commentary'), 'Commentary generation');
  assert.equal(stageLabel('script'), 'Script generation');
  assert.equal(stageLabel('mystery_stage'), 'mystery_stage');
  assert.equal(Object.keys(STAGE_LABELS).length, 6);
});

test('started resets to a fresh processing state', () => {
  const dirty = {
    ...createInitialRunState(),
    status: 'failed',
    error: { stage: 'x', message: 'y' },
  };
  const next = applyWorkerMessage(dirty, {
    type: 'started',
    message: 'Processing clip.mp4',
  });
  assert.equal(next.status, 'processing');
  assert.equal(next.detail, 'Processing clip.mp4');
  assert.equal(next.error, null);
  assert.equal(next.result, null);
  assert.deepEqual(next.stages, {});
});

test('progress tracks stage-level counts only', () => {
  let state = createInitialRunState();
  state = applyWorkerMessage(state, {
    type: 'progress',
    stage: 'frame_extraction',
    completed: 0,
    total: 5,
  });
  assert.equal(state.status, 'processing');
  assert.equal(state.currentStage, 'frame_extraction');
  assert.equal(state.completedCount, 0);
  assert.equal(state.totalStages, 5);
});

test('stage_completed records done and skipped honestly', () => {
  let state = applyWorkerMessage(createInitialRunState(), {
    type: 'progress',
    stage: 'vision_analysis',
    completed: 1,
    total: 5,
  });
  state = applyWorkerMessage(state, {
    type: 'stage_completed',
    stage: 'vision_analysis',
    completed: 2,
    total: 5,
    detail: { skipped: true },
  });
  assert.equal(state.stages.vision_analysis.status, 'skipped');
  assert.equal(state.currentStage, null);
  state = applyWorkerMessage(state, {
    type: 'stage_completed',
    stage: 'frame_extraction',
    completed: 1,
    total: 5,
    detail: { frames: 3 },
  });
  assert.equal(state.stages.frame_extraction.status, 'done');
  assert.equal(state.stages.frame_extraction.detail.frames, 3);
});

test('result stores data and ignores invented fields', () => {
  const data = {
    frames: 3,
    observations: 0,
    events: 0,
    commentary_lines: 0,
    script: 'skipped',
  };
  const state = applyWorkerMessage(createInitialRunState(), {
    type: 'result',
    data,
  });
  assert.equal(state.status, 'completed');
  assert.deepEqual(state.result, data);
  assert.equal(state.error, null);
});

test('error stores stage and message', () => {
  const state = applyWorkerMessage(createInitialRunState(), {
    type: 'error',
    stage: 'vision_analysis',
    message: 'Missing API key.',
  });
  assert.equal(state.status, 'failed');
  assert.deepEqual(state.error, {
    stage: 'vision_analysis',
    message: 'Missing API key.',
  });
});

test('worker-log appends text and caps the buffer', () => {
  let state = createInitialRunState();
  for (let i = 0; i < MAX_WORKER_LOGS + 10; i += 1) {
    state = applyWorkerMessage(state, { type: 'worker-log', text: `line ${i}` });
  }
  assert.equal(state.workerLogs.length, MAX_WORKER_LOGS);
  assert.equal(state.workerLogs.at(-1), `line ${MAX_WORKER_LOGS + 9}`);
  const unchanged = applyWorkerMessage(state, { type: 'worker-log', text: '' });
  assert.equal(unchanged, state);
});

test('worker-exit honours cancellation and terminal states', () => {
  const cancelled = applyWorkerMessage(
    { ...createInitialRunState(), status: 'processing' },
    { type: 'worker-exit', code: 130, cancelled: true },
  );
  assert.equal(cancelled.status, 'cancelled');

  const doneState = {
    ...createInitialRunState(),
    status: 'completed',
    settled: false,
    result: { frames: 1 },
  };
  const completed = applyWorkerMessage(doneState, {
    type: 'worker-exit',
    code: 0,
    cancelled: false,
  });
  assert.equal(completed.status, 'completed');
  assert.equal(completed.settled, true);
  assert.deepEqual(completed.result, { frames: 1 });

  const crashed = applyWorkerMessage(
    { ...createInitialRunState(), status: 'processing' },
    { type: 'worker-exit', code: 1, cancelled: false },
  );
  assert.equal(crashed.status, 'failed');
  assert.match(crashed.error.message, /code 1/);
});

test('settled tracks the worker lifecycle for safe restarts', () => {
  assert.equal(createInitialRunState().settled, true);
  const started = applyWorkerMessage(createInitialRunState(), {
    type: 'started',
    message: 'Processing clip.mp4',
  });
  assert.equal(started.settled, false);
  const progressed = applyWorkerMessage(started, {
    type: 'progress',
    stage: 'frame_extraction',
    completed: 0,
    total: 5,
  });
  assert.equal(progressed.settled, false);
  const done = applyWorkerMessage(progressed, {
    type: 'result',
    data: { frames: 1 },
  });
  assert.equal(done.settled, false);
  const exited = applyWorkerMessage(done, {
    type: 'worker-exit',
    code: 0,
    cancelled: false,
  });
  assert.equal(exited.settled, true);
  const failed = applyWorkerMessage(progressed, {
    type: 'error',
    stage: 'vision_analysis',
    message: 'Missing API key.',
  });
  assert.equal(failed.settled, false);
  const failedExit = applyWorkerMessage(failed, {
    type: 'worker-exit',
    code: 1,
    cancelled: false,
  });
  assert.equal(failedExit.settled, true);
  const spawnFailed = applyWorkerMessage(createInitialRunState(), {
    type: 'error',
    stage: 'spawn',
    message: 'Could not launch.',
  });
  assert.equal(spawnFailed.settled, true);
});

test('malformed and unknown messages never corrupt state', () => {
  const state = createInitialRunState();
  assert.equal(applyWorkerMessage(state, null), state);
  assert.equal(applyWorkerMessage(state, { type: 42 }), state);
  assert.equal(applyWorkerMessage(state, { type: 'nope' }), state);
  assert.equal(
    applyWorkerMessage(state, { type: 'stage_completed' }),
    state,
  );
});

test('format helpers handle names, extensions, and sizes', () => {
  assert.equal(fileNameOf('D:\\clips\\match one.MP4'), 'match one.MP4');
  assert.equal(fileNameOf('/tmp/a/b.webm'), 'b.webm');
  assert.equal(fileNameOf(''), '');
  assert.equal(extensionOf('clip.MP4'), 'mp4');
  assert.equal(extensionOf('noext'), '');
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(1048576), '1 MB');
  assert.equal(formatBytes(-1), '—');
  assert.equal(formatBytes(Number.NaN), '—');
});

test('timeAgo renders relative times without throwing', () => {
  const now = Date.parse('2026-10-31T12:00:00.000Z');
  assert.equal(timeAgo('2026-10-31T11:59:30.000Z', now), 'just now');
  assert.equal(timeAgo('2026-10-31T11:55:00.000Z', now), '5m ago');
  assert.equal(timeAgo('2026-10-31T10:00:00.000Z', now), '2h ago');
  assert.equal(timeAgo('2026-10-28T12:00:00.000Z', now), '3d ago');
  assert.equal(timeAgo('2026-08-31T12:00:00.000Z', now), '2mo ago');
  assert.equal(timeAgo('2024-10-31T12:00:00.000Z', now), '2y ago');
  assert.equal(timeAgo('not-a-date', now), '—');
  assert.equal(timeAgo(null, now), '—');
});

test('projectMediaUrl builds scoped protocol URLs', () => {
  assert.equal(
    projectMediaUrl('abc-123', 'frames', 'frame_00003.jpg'),
    'project://abc-123/frames/frame_00003.jpg',
  );
});

test('fileUrlOf builds file URLs from trusted absolute paths', () => {
  assert.equal(
    fileUrlOf('D:\\clips\\match one.mp4'),
    'file:///D:/clips/match%20one.mp4',
  );
  assert.equal(fileUrlOf('/tmp/a/b.webm'), 'file:///tmp/a/b.webm');
  assert.equal(fileUrlOf(''), '');
  assert.equal(fileUrlOf('relative/clip.mp4'), '');
  assert.equal(fileUrlOf(null), '');
});
