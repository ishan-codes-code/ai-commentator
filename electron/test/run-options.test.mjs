import assert from 'node:assert/strict';
import test from 'node:test';

import {
  RUN_MODES,
  VISION_FRAME_LIMIT,
  buildProcessOptions,
  defaultRunMode,
  isAiMode,
} from '../src/lib/run-options.js';

test('AI mode carries the explicit 10-frame limit', () => {
  assert.equal(VISION_FRAME_LIMIT, 10);
  assert.deepEqual(buildProcessOptions(RUN_MODES.AI), {
    skipVision: false,
    limit: 10,
  });
});

test('offline mode skips vision with no limit key', () => {
  const options = buildProcessOptions(RUN_MODES.OFFLINE);
  assert.equal(options.skipVision, true);
  assert.ok(!('limit' in options));
});

test('unknown modes fall back to offline', () => {
  assert.equal(buildProcessOptions('turbo').skipVision, true);
  assert.equal(buildProcessOptions(undefined).skipVision, true);
});

test('default mode follows key configuration', () => {
  assert.equal(defaultRunMode(true), RUN_MODES.AI);
  assert.equal(defaultRunMode(false), RUN_MODES.OFFLINE);
  assert.equal(isAiMode(RUN_MODES.AI), true);
  assert.equal(isAiMode(RUN_MODES.OFFLINE), false);
});
