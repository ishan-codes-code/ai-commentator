import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ADAPTIVE_DEFAULT_MAX_FRAMES,
  ADAPTIVE_HARD_MAX_FRAMES,
  RUN_MODES,
  buildProcessOptions,
  clampAdaptiveMaxFrames,
  defaultRunMode,
  isAdaptiveMode,
  isAiMode,
  isPaidMode,
} from '../src/lib/run-options.js';

test('mode predicates distinguish paid from offline runs', () => {
  assert.equal(isAiMode(RUN_MODES.AI), true);
  assert.equal(isAdaptiveMode(RUN_MODES.ADAPTIVE), true);
  assert.equal(isAiMode(RUN_MODES.ADAPTIVE), false);
  assert.equal(isAdaptiveMode(RUN_MODES.AI), false);
  assert.equal(isPaidMode(RUN_MODES.AI), true);
  assert.equal(isPaidMode(RUN_MODES.ADAPTIVE), true);
  assert.equal(isPaidMode(RUN_MODES.OFFLINE), false);
  assert.equal(isPaidMode('mystery'), false);
});

test('default mode never selects adaptive implicitly', () => {
  assert.equal(defaultRunMode(true), RUN_MODES.AI);
  assert.equal(defaultRunMode(false), RUN_MODES.OFFLINE);
});

test('adaptive options carry the validated sampling bound', () => {
  assert.deepEqual(buildProcessOptions(RUN_MODES.ADAPTIVE), {
    adaptive: true,
    adaptiveMaxFrames: ADAPTIVE_DEFAULT_MAX_FRAMES,
  });
  assert.deepEqual(
    buildProcessOptions(RUN_MODES.ADAPTIVE, { adaptiveMaxFrames: 5 }),
    { adaptive: true, adaptiveMaxFrames: 5 },
  );
  // Fractions floor; the hard cap holds; garbage falls back to default.
  assert.equal(
    buildProcessOptions(RUN_MODES.ADAPTIVE, { adaptiveMaxFrames: 5.9 })
      .adaptiveMaxFrames,
    5,
  );
  assert.equal(
    buildProcessOptions(RUN_MODES.ADAPTIVE, { adaptiveMaxFrames: 10 ** 9 })
      .adaptiveMaxFrames,
    ADAPTIVE_HARD_MAX_FRAMES,
  );
  for (const bad of [0, -3, '', 'many', NaN, null, undefined]) {
    assert.equal(
      buildProcessOptions(RUN_MODES.ADAPTIVE, { adaptiveMaxFrames: bad })
        .adaptiveMaxFrames,
      ADAPTIVE_DEFAULT_MAX_FRAMES,
    );
  }
});

test('clamp keeps bounds inside the engine hard cap', () => {
  assert.equal(ADAPTIVE_DEFAULT_MAX_FRAMES, 10);
  assert.equal(ADAPTIVE_HARD_MAX_FRAMES, 10000);
  assert.equal(clampAdaptiveMaxFrames(1), 1);
  assert.equal(clampAdaptiveMaxFrames('7'), 7);
  assert.equal(clampAdaptiveMaxFrames(' 12 '), 12);
});

test('AI and offline mappings are unchanged', () => {
  assert.deepEqual(buildProcessOptions(RUN_MODES.AI), {
    skipVision: false,
    limit: 10,
  });
  assert.deepEqual(buildProcessOptions(RUN_MODES.OFFLINE), {
    skipVision: true,
  });
  assert.deepEqual(buildProcessOptions('mystery'), { skipVision: true });
});
