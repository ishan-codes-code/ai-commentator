import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { createWorkerManager } from '../src/python-worker.mjs';
import { resolvePythonExecutable } from '../src/worker-protocol.mjs';

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

function makeFakeStdin({ writable = true, failWrite = false } = {}) {
  const stdin = new EventEmitter();
  stdin.writes = [];
  stdin.destroyed = false;
  stdin.writable = writable;
  stdin.write = (data) => {
    if (failWrite) {
      throw new Error('EPIPE: broken pipe');
    }
    stdin.writes.push(String(data));
    return true;
  };
  return stdin;
}

function makeFakeChild({ stdin } = {}) {
  const child = new EventEmitter();
  child.pid = 4242;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  if (stdin !== null) {
    child.stdin = stdin === undefined ? makeFakeStdin() : stdin;
  }
  child.killCalls = [];
  child.kill = (signal) => {
    child.killCalls.push(signal);
    return true;
  };
  return child;
}

function startWithFakeChild(child, callbacks = {}) {
  let captured = null;
  const manager = createWorkerManager({
    spawnFn: (executable, argv, options) => {
      captured = { executable, argv, options };
      return child;
    },
  });
  const started = manager.start({
    executable: 'python',
    argv: ['worker.py', 'clip.mp4'],
    onMessage: callbacks.onMessage ?? (() => {}),
    onStderr: callbacks.onStderr ?? (() => {}),
    onExit: callbacks.onExit ?? (() => {}),
  });
  assert.equal(started.started, true);
  return { manager, captured };
}

const STOP_LINE = '{"type":"stop","version":1}\n';

test('spawn opens a stdin pipe for control messages', () => {
  const { manager, captured } = startWithFakeChild(makeFakeChild());
  assert.deepEqual(captured.options.stdio, ['pipe', 'pipe', 'pipe']);
});

test('requestStop writes the documented stop line to stdin', () => {
  const child = makeFakeChild();
  const { manager } = startWithFakeChild(child);
  const answer = manager.requestStop();
  assert.deepEqual(answer, { requested: true, reason: 'stop-requested' });
  assert.deepEqual(child.stdin.writes, [STOP_LINE]);
});

test('requestStop with no job reports no-active-job', () => {
  const manager = createWorkerManager();
  assert.deepEqual(manager.requestStop(), { requested: false, reason: 'no-active-job' });
});

test('requestStop with closed stdin is safe', () => {
  const closed = makeFakeStdin();
  closed.destroyed = true;
  const { manager } = startWithFakeChild(makeFakeChild({ stdin: closed }));
  assert.deepEqual(manager.requestStop(), { requested: false, reason: 'stdin-unavailable' });
  assert.deepEqual(closed.writes, []);
});

test('requestStop with missing stdin is safe', () => {
  const { manager } = startWithFakeChild(makeFakeChild({ stdin: null }));
  assert.deepEqual(manager.requestStop(), { requested: false, reason: 'stdin-unavailable' });
});

test('requestStop with failing writes reports write-failed', () => {
  const broken = makeFakeStdin({ failWrite: true });
  const { manager } = startWithFakeChild(makeFakeChild({ stdin: broken }));
  assert.deepEqual(manager.requestStop(), { requested: false, reason: 'write-failed' });
  // A failed delivery is retryable: nothing was recorded as sent.
  assert.deepEqual(manager.requestStop(), { requested: false, reason: 'write-failed' });
});

test('repeated requestStop is idempotent', () => {
  const child = makeFakeChild();
  const { manager } = startWithFakeChild(child);
  assert.deepEqual(manager.requestStop(), { requested: true, reason: 'stop-requested' });
  assert.deepEqual(manager.requestStop(), { requested: true, reason: 'already-requested' });
  assert.deepEqual(child.stdin.writes, [STOP_LINE]);
});

test('stdin stream errors never crash the manager', () => {
  const child = makeFakeChild();
  const { manager } = startWithFakeChild(child);
  child.stdin.emit('error', new Error('EPIPE'));
  assert.deepEqual(manager.requestStop(), { requested: true, reason: 'stop-requested' });
});

test('requestStop never cancels and never arms escalation', async () => {
  const child = makeFakeChild();
  let exited = null;
  const { manager } = startWithFakeChild(child, { onExit: (outcome) => { exited = outcome; } });
  manager.requestStop();
  assert.deepEqual(child.killCalls, []);
  assert.equal(exited, null);
  child.emit('close', 0, null);
  assert.equal(exited.cancelled, false);
  assert.equal(exited.code, 0);
});

test('manager waits for the actual child exit after requestStop', () => {
  const child = makeFakeChild();
  let exits = 0;
  const { manager } = startWithFakeChild(child, { onExit: () => { exits += 1; } });
  manager.requestStop();
  assert.equal(exits, 0);
  child.emit('close', 0, null);
  assert.equal(exits, 1);
  assert.equal(manager.isBusy(), false);
});

test('a stale record cannot reach a later run', () => {
  const childA = makeFakeChild();
  const childB = makeFakeChild();
  const pending = [childA, childB];
  let spawnCount = 0;
  const manager = createWorkerManager({
    spawnFn: () => {
      spawnCount += 1;
      return pending.shift();
    },
  });
  const launch = () => {
    const started = manager.start({
      executable: 'python',
      argv: ['worker.py', 'clip.mp4'],
      onExit: () => {},
    });
    assert.equal(started.started, true);
  };
  launch();
  assert.deepEqual(manager.requestStop(), { requested: true, reason: 'stop-requested' });
  childA.emit('close', 0, null);
  launch();
  assert.deepEqual(manager.requestStop(), { requested: true, reason: 'stop-requested' });
  assert.deepEqual(childA.stdin.writes, [STOP_LINE]);
  assert.deepEqual(childB.stdin.writes, [STOP_LINE]);
  assert.equal(spawnCount, 2);
});

test('cancel still terminates forcefully after a cooperative stop', async () => {
  const child = makeFakeChild();
  let exited = null;
  const { manager } = startWithFakeChild(child, { onExit: (outcome) => { exited = outcome; } });
  assert.deepEqual(manager.requestStop(), { requested: true, reason: 'stop-requested' });
  const cancelPromise = manager.cancel({ killGraceMs: 20 });
  child.emit('close', null, 'SIGTERM');
  assert.equal(await cancelPromise, true);
  assert.equal(exited.cancelled, true);
  assert.ok(child.killCalls.includes('SIGTERM'));
});

test('stop line crosses a real OS pipe byte-for-byte', async () => {
  const manager = createWorkerManager();
  const logs = [];
  let resolveExit;
  const exited = new Promise((resolve) => { resolveExit = resolve; });
  manager.start({
    executable: NODE,
    argv: [
      '-e',
      'let buf = "";' +
      'process.stdin.on("data", (chunk) => {' +
      '  buf += chunk;' +
      '  let index;' +
      '  while ((index = buf.indexOf("\\n")) >= 0) {' +
      '    console.error("GOT:" + buf.slice(0, index));' +
      '    buf = buf.slice(index + 1);' +
      '  }' +
      '});' +
      'setTimeout(() => process.exit(0), 1500);',
    ],
    onMessage: () => {},
    onStderr: (text) => logs.push(text),
    onExit: (outcome) => resolveExit(outcome),
  });
  const answer = manager.requestStop();
  assert.deepEqual(answer, { requested: true, reason: 'stop-requested' });
  const outcome = await exited;
  assert.equal(outcome.code, 0);
  assert.equal(outcome.cancelled, false);
  assert.match(logs.join(''), /GOT:\{"type":"stop","version":1\}/);
});

test('cooperative stop reaches a real Python adaptive worker', async () => {
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-commentator-stop-'));
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const repoRoot = path.resolve(here, '..', '..');
    const resolved = resolvePythonExecutable(repoRoot);
    assert.ok(resolved.executable, 'venv python required for the joined stop test');
    const videoPath = path.join(tmpRoot, 'clip.mp4');
    execFileSync(resolved.executable, [
      '-c',
      [
        'import sys',
        'import cv2',
        'import numpy',
        'writer = cv2.VideoWriter(sys.argv[1], cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))',
        'frame = numpy.zeros((48, 64, 3), dtype=numpy.uint8)',
        'for _ in range(70):',
        '    writer.write(frame)',
        'writer.release()',
      ].join('\n'),
      videoPath,
    ]);
    const shimDir = path.join(tmpRoot, 'shim');
    fs.mkdirSync(shimDir);
    fs.writeFileSync(
      path.join(shimDir, 'openai.py'),
      [
        '"""Process-boundary fake: canned vision answers, file-synced, no network."""',
        'import json',
        'import os',
        'import time',
        '',
        '',
        'class APIError(Exception):',
        '    def __init__(self, message="", request=None, body=None):',
        '        super(APIError, self).__init__(message)',
        '        self.message = message',
        '        self.status_code = None',
        '',
        '',
        'class _Message:',
        '    def __init__(self, content):',
        '        self.content = content',
        '',
        '',
        'class _Choice:',
        '    def __init__(self, message):',
        '        self.message = message',
        '',
        '',
        'class _Completion:',
        '    def __init__(self, choices):',
        '        self.choices = choices',
        '',
        '',
        'class _RawResponse:',
        '    status_code = 200',
        '    def __init__(self, completion):',
        '        self._completion = completion',
        '    def parse(self):',
        '        return self._completion',
        '',
        '',
        'class _Completions:',
        '    def __init__(self):',
        '        self.with_raw_response = self',
        '    def create(self, **kwargs):',
        '        count = os.environ.get("SHIM_COUNT_FILE")',
        '        if count:',
        '            handle = open(count, "a", encoding="utf-8")',
        '            handle.write("x" + chr(10))',
        '            handle.close()',
        '        started = os.environ.get("SHIM_STARTED_FILE")',
        '        if started:',
        '            open(started, "a", encoding="utf-8").close()',
        '        release = os.environ.get("SHIM_RELEASE_FILE")',
        '        if release:',
        '            deadline = time.time() + 120.0',
        '            while not os.path.exists(release):',
        '                if time.time() > deadline:',
        '                    raise TimeoutError("release never arrived")',
        '                time.sleep(0.05)',
        '        text = kwargs["messages"][0]["content"][0]["text"]',
        '        head = text.split("This frame is at ")[1]',
        '        stamp = float(head.split(" seconds")[0])',
        '        timestamp = int(stamp) if float(stamp).is_integer() else float(stamp)',
        '        document = {',
        '            "observation": {',
        '                "schema_version": "observation-v1",',
        '                "timestamp": timestamp,',
        '                "observations": [{',
        '                    "category": "environment",',
        '                    "description": "Fixture backdrop.",',
        '                    "confidence": "high",',
        '                    "evidence": "Shim frame.",',
        '                }],',
        '                "uncertainties": [],',
        '            },',
        '            "sampling": {"next_sampling_speed": "NORMAL", "reason": "Shim."},',
        '        }',
        '        message = _Message(json.dumps(document))',
        '        return _RawResponse(_Completion([_Choice(message)]))',
        '',
        '',
        'class _Chat:',
        '    def __init__(self):',
        '        self.completions = _Completions()',
        '',
        '',
        'class OpenAI:',
        '    def __init__(self, *args, **kwargs):',
        '        self.chat = _Chat()',
        '    def close(self):',
        '        pass',
        '',
      ].join('\n'),
    );
    const outDir = path.join(tmpRoot, 'out');
    const startedFile = path.join(tmpRoot, 'started');
    const releaseFile = path.join(tmpRoot, 'release');
    const countFile = path.join(tmpRoot, 'count');
    const savedKey = process.env.BASETEN_API_KEY;
    const savedPath = process.env.PYTHONPATH;
    process.env.BASETEN_API_KEY = 'test-dummy-key-never-a-secret';
    process.env.PYTHONPATH = savedPath ? shimDir + path.delimiter + savedPath : shimDir;
    process.env.SHIM_STARTED_FILE = startedFile;
    process.env.SHIM_RELEASE_FILE = releaseFile;
    process.env.SHIM_COUNT_FILE = countFile;
    const manager = createWorkerManager();
    const messages = [];
    const logs = [];
    let resolveExit;
    const exited = new Promise((resolve) => { resolveExit = resolve; });
    try {
      manager.start({
        executable: resolved.executable,
        argv: [
          path.join(repoRoot, 'python', 'worker.py'),
          videoPath,
          '--output-dir', outDir,
          '--adaptive',
          '--adaptive-max-frames', '10',
        ],
        onMessage: (message) => messages.push(message),
        onStderr: (text) => logs.push(text),
        onExit: (outcome) => resolveExit(outcome),
      });
      const deadline = Date.now() + 90000;
      while (!fs.existsSync(startedFile)) {
        if (Date.now() > deadline) {
          throw new Error('timed out waiting for the in-flight request');
        }
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      const answer = manager.requestStop();
      assert.deepEqual(answer, { requested: true, reason: 'stop-requested' });
      fs.writeFileSync(releaseFile, '');
      const outcome = await Promise.race([
        exited,
        new Promise((_, reject) => setTimeout(() => reject(new Error('worker did not exit')), 120000)),
      ]);
      assert.equal(outcome.code, 0);
      assert.equal(outcome.cancelled, false);
      assert.equal(messages.filter((m) => m.type === 'stopped').length, 1);
      assert.equal(messages.filter((m) => m.type === 'result').length, 0);
      assert.equal(messages.filter((m) => m.type === 'error').length, 0);
      const stopped = messages.find((m) => m.type === 'stopped');
      assert.equal(stopped.data.status, 'stopped');
      const obsDir = path.join(outDir, 'observations');
      const observation = JSON.parse(fs.readFileSync(path.join(obsDir, 'frame_00000.json'), 'utf-8'));
      assert.equal(observation.timestamp, 0);
      const manifest = JSON.parse(fs.readFileSync(path.join(obsDir, 'adaptive_run.json'), 'utf-8'));
      assert.equal(manifest.status, 'stopped');
      assert.deepEqual(manifest.requested_timestamps, [0]);
      assert.equal(fs.readFileSync(countFile, 'utf-8').split('\n').filter(Boolean).length, 1);
      assert.ok(!logs.join('').includes('test-dummy-key-never-a-secret'));
    } finally {
      if (savedKey === undefined) {
        delete process.env.BASETEN_API_KEY;
      } else {
        process.env.BASETEN_API_KEY = savedKey;
      }
      if (savedPath === undefined) {
        delete process.env.PYTHONPATH;
      } else {
        process.env.PYTHONPATH = savedPath;
      }
      delete process.env.SHIM_STARTED_FILE;
      delete process.env.SHIM_RELEASE_FILE;
      delete process.env.SHIM_COUNT_FILE;
      await manager.cancel({ killGraceMs: 50 });
      fs.rmSync(tmpRoot, { recursive: true, force: true });
    }
  } catch (error) {
    fs.rmSync(tmpRoot, { recursive: true, force: true });
    throw error;
  }
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
