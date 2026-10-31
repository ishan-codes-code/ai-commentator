import assert from 'node:assert/strict';
import test from 'node:test';

import { createWorkerManager } from '../src/python-worker.mjs';

const NODE = process.execPath;

function runToExit(manager, argv, { onMessage, onStderr } = {}) {
  const messages = [];
  const logs = [];
  return new Promise((resolve) => {
    const started = manager.start({
      executable: NODE,
      argv,
      onMessage: (m) => {
        messages.push(m);
        if (typeof onMessage === 'function') {
          onMessage(m);
        }
      },
      onStderr: (text) => {
        logs.push(text);
        if (typeof onStderr === 'function') {
          onStderr(text);
        }
      },
      onExit: (outcome) => resolve({ outcome, messages, logs }),
    });
    assert.equal(started.started, true);
  });
}

test('start delivers NDJSON messages and a clean exit', async () => {
  const manager = createWorkerManager();
  const { outcome, messages } = await runToExit(manager, [
    '-e',
    'console.log(JSON.stringify({type:"started"}));' +
      'console.log(JSON.stringify({type:"result",data:{frames:1}}));',
  ]);
  assert.deepEqual(messages, [{ type: 'started' }, { type: 'result', data: { frames: 1 } }]);
  assert.equal(outcome.code, 0);
  assert.equal(outcome.cancelled, false);
  assert.equal(manager.isBusy(), false);
});

test('split writes across ticks are reassembled', async () => {
  const manager = createWorkerManager();
  const { outcome, messages } = await runToExit(manager, [
    '-e',
    'process.stdout.write("{\\"type\\":\\"prog");' +
      'setTimeout(() => { process.stdout.write("ress\\"}\\n"); }, 50);',
  ]);
  assert.deepEqual(messages, [{ type: 'progress' }]);
  assert.equal(outcome.code, 0);
});

test('stderr is forwarded and unknown types become protocol errors', async () => {
  const manager = createWorkerManager();
  const { outcome, messages, logs } = await runToExit(manager, [
    '-e',
    'console.error("engine diagnostic");' +
      'console.log(JSON.stringify({type:"mystery"}));',
  ]);
  assert.match(logs.join(''), /engine diagnostic/);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].type, 'error');
  assert.equal(messages[0].stage, 'protocol');
  assert.equal(outcome.code, 0);
});

test('second start while busy is refused without spawning', async () => {
  const manager = createWorkerManager();
  let exited = null;
  const first = manager.start({
    executable: NODE,
    argv: ['-e', 'setInterval(() => {}, 1000);'],
    onExit: (outcome) => {
      exited = outcome;
    },
  });
  assert.equal(first.started, true);
  const second = manager.start({ executable: NODE, argv: ['-e', ''] });
  assert.deepEqual(second, { started: false, reason: 'busy' });
  assert.equal(manager.isBusy(), true);

  const stopped = await manager.cancel({ killGraceMs: 200 });
  assert.equal(stopped, true);
  assert.equal(manager.isBusy(), false);
  assert.equal(exited && exited.cancelled, true);
});

test('cancel with no job resolves false', async () => {
  const manager = createWorkerManager();
  assert.equal(await manager.cancel(), false);
});

test('spawn failure reports a spawn error and frees the manager', async () => {
  const manager = createWorkerManager();
  const outcome = await new Promise((resolve) => {
    const started = manager.start({
      executable: '/nonexistent/python-xyz-123',
      argv: [],
      onExit: resolve,
    });
    assert.equal(started.started, true);
  });
  assert.ok(outcome.spawnError);
  assert.equal(manager.isBusy(), false);
  // The manager is usable again after the failure.
  const retry = manager.start({
    executable: NODE,
    argv: ['-e', ''],
    onExit: () => {},
  });
  assert.equal(retry.started, true);
  await manager.cancel({ killGraceMs: 200 });
});

test('non-zero exit is reported with its code', async () => {
  const manager = createWorkerManager();
  const { outcome, messages } = await runToExit(manager, [
    '-e',
    'console.log(JSON.stringify({type:"error",stage:"vision_analysis",message:"boom"}));' +
      'process.exitCode = 1;',
  ]);
  assert.deepEqual(messages, [
    { type: 'error', stage: 'vision_analysis', message: 'boom' },
  ]);
  assert.equal(outcome.code, 1);
  assert.equal(outcome.cancelled, false);
});
