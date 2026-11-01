import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  buildWorkerArgv,
  createLineParser,
  isValidWorkerMessage,
  isVisionConfigured,
  resolvePythonExecutable,
  resolveWorkerScript,
  validateVideoInput,
} from '../src/worker-protocol.mjs';

test('parser assembles a message split across chunks', () => {
  const seen = [];
  const parser = createLineParser({ onMessage: (m) => seen.push(m) });
  assert.deepEqual(parser.feed('{"type": "star'), []);
  assert.deepEqual(parser.feed('ted", "message": "hi"}\n'), [
    { type: 'started', message: 'hi' },
  ]);
  assert.deepEqual(seen, [{ type: 'started', message: 'hi' }]);
});

test('parser handles several messages in one chunk and CRLF', () => {
  const seen = [];
  const parser = createLineParser({ onMessage: (m) => seen.push(m) });
  const out = parser.feed(
    '{"type":"progress","stage":"a"}\r\n\r\n{"type":"result","data":{}}\n',
  );
  assert.equal(out.length, 2);
  assert.deepEqual(seen, out);
});

test('parser skips invalid lines and keeps going', () => {
  const seen = [];
  const invalid = [];
  const parser = createLineParser({
    onMessage: (m) => seen.push(m),
    onInvalid: (line) => invalid.push(line),
  });
  const out = parser.feed('FPS: 12.50\n{"type":"progress","stage":"a"}\n');
  assert.deepEqual(out, [{ type: 'progress', stage: 'a' }]);
  assert.deepEqual(invalid, ['FPS: 12.50']);
  assert.equal(parser.invalidLines(), 1);
});

test('flush dispatches a final unterminated line', () => {
  const parser = createLineParser({});
  assert.deepEqual(parser.feed('{"type":"started"}\n'), [{ type: 'started' }]);
  assert.deepEqual(parser.flush(), []);
  parser.feed('{"type":"result","data":{}}');
  assert.deepEqual(parser.flush(), [{ type: 'result', data: {} }]);
  assert.deepEqual(parser.flush(), []);
});

test('validateVideoInput accepts supported extensions case-insensitively', () => {
  assert.equal(validateVideoInput('clip.MP4'), null);
  assert.equal(validateVideoInput('/tmp/a/b.webm'), null);
  assert.match(validateVideoInput(''), /non-empty/);
  assert.match(validateVideoInput(42), /non-empty/);
  assert.match(validateVideoInput('clip.txt'), /Unsupported/);
  assert.match(validateVideoInput('noextension'), /Unsupported/);
  // Path traversal shape is the worker's problem to reject by existence;
  // the IPC layer only enforces type and extension.
  assert.equal(validateVideoInput('../other/clip.mp4'), null);
});

test('isValidWorkerMessage accepts only known types', () => {
  assert.equal(isValidWorkerMessage({ type: 'started' }), true);
  assert.equal(isValidWorkerMessage({ type: 'result', data: {} }), true);
  assert.equal(isValidWorkerMessage({ type: 'nope' }), false);
  assert.equal(isValidWorkerMessage(null), false);
  assert.equal(isValidWorkerMessage('started'), false);
});

test('resolvePythonExecutable reports the venv interpreter or its absence', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'venv-resolve-'));
  try {
    const missing = resolvePythonExecutable(tmp);
    assert.equal(missing.executable, null);
    assert.match(missing.missing, /\.venv/);

    const binDir =
      process.platform === 'win32'
        ? path.join(tmp, '.venv', 'Scripts')
        : path.join(tmp, '.venv', 'bin');
    fs.mkdirSync(binDir, { recursive: true });
    const exe =
      process.platform === 'win32'
        ? path.join(binDir, 'python.exe')
        : path.join(binDir, 'python');
    fs.writeFileSync(exe, '');
    const found = resolvePythonExecutable(tmp);
    assert.equal(found.executable, exe);
    assert.equal(found.source, 'venv');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('resolveWorkerScript reports presence or absence', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'worker-resolve-'));
  try {
    const missing = resolveWorkerScript(tmp);
    assert.equal(missing.script, null);
    assert.match(missing.missing, /worker\.py/);

    fs.mkdirSync(path.join(tmp, 'python'), { recursive: true });
    fs.writeFileSync(path.join(tmp, 'python', 'worker.py'), '');
    const found = resolveWorkerScript(tmp);
    assert.ok(found.script.endsWith('worker.py'));
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('stopped is a known worker message type', () => {
  assert.equal(isValidWorkerMessage({ type: 'stopped', data: {} }), true);
});

test('buildWorkerArgv passes adaptive flags', () => {
  assert.deepEqual(
    buildWorkerArgv('worker.py', 'clip.mp4', {
      adaptive: true,
      adaptiveMaxFrames: 3,
    }),
    ['worker.py', 'clip.mp4', '--adaptive', '--adaptive-max-frames', '3'],
  );
});

test('buildWorkerArgv rejects non-positive adaptiveMaxFrames', () => {
  for (const adaptiveMaxFrames of [0, -2, 2.5, 'many', NaN]) {
    assert.throws(
      () => buildWorkerArgv('worker.py', 'clip.mp4', { adaptiveMaxFrames }),
      /positive integer/,
    );
  }
});

test('buildWorkerArgv omits adaptive flags unless requested', () => {
  assert.deepEqual(
    buildWorkerArgv('worker.py', 'clip.mp4', { skipVision: true }),
    ['worker.py', 'clip.mp4', '--skip-vision'],
  );
});

test('buildWorkerArgv passes explicit limit for AI runs', () => {
  assert.deepEqual(
    buildWorkerArgv('worker.py', 'clip.mp4', {
      outputDir: '/out',
      limit: 10,
      skipVision: false,
    }),
    ['worker.py', 'clip.mp4', '--output-dir', '/out', '--limit', '10'],
  );
});

test('buildWorkerArgv omits vision flags for offline runs', () => {
  assert.deepEqual(
    buildWorkerArgv('worker.py', 'clip.mp4', {
      outputDir: '/out',
      skipVision: true,
    }),
    ['worker.py', 'clip.mp4', '--output-dir', '/out', '--skip-vision'],
  );
});

test('buildWorkerArgv rejects non-positive limits', () => {
  for (const limit of [0, -3, 2.5, 'many', NaN]) {
    assert.throws(
      () => buildWorkerArgv('worker.py', 'clip.mp4', { limit }),
      /positive integer/,
    );
  }
});

test('buildWorkerArgv never invents flags from unknown options', () => {
  assert.deepEqual(
    buildWorkerArgv('worker.py', 'clip.mp4', {
      skipVision: true,
      extra: '--exec-evil',
    }),
    ['worker.py', 'clip.mp4', '--skip-vision'],
  );
});

test('isVisionConfigured reads only presence of the key', () => {
  assert.equal(isVisionConfigured({ BASETEN_API_KEY: 'secret' }), true);
  assert.equal(isVisionConfigured({ BASETEN_API_KEY: '  ' }), false);
  assert.equal(isVisionConfigured({}), false);
  assert.equal(isVisionConfigured(null), false);
});
