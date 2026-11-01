/**
 * Pure run-mode mapping for the workspace processing selector.
 *
 * AI analysis runs vision on exactly VISION_FRAME_LIMIT frames; offline
 * extraction skips vision; adaptive sampling captures one frame per
 * vision request up to a caller-supplied maximum. No IPC, no filesystem,
 * no secrets here — the main process validates and enforces these
 * options before spawning.
 */

export const VISION_FRAME_LIMIT = 10;

/**
 * Adaptive sampling bounds (M4.2-B). The UI default is deliberately
 * below the engine default (60): every sampled frame costs at most one
 * paid vision request. The engine still enforces its own hard cap
 * regardless of what the UI sends.
 */
export const ADAPTIVE_DEFAULT_MAX_FRAMES = 10;
export const ADAPTIVE_HARD_MAX_FRAMES = 10000;

export const RUN_MODES = {
  AI: 'ai',
  OFFLINE: 'offline',
  ADAPTIVE: 'adaptive',
};

export function isAiMode(mode) {
  return mode === RUN_MODES.AI;
}

export function isAdaptiveMode(mode) {
  return mode === RUN_MODES.ADAPTIVE;
}

/** Any mode that spends paid vision requests. */
export function isPaidMode(mode) {
  return isAiMode(mode) || isAdaptiveMode(mode);
}

/** Default to AI analysis only when a key is configured. */
export function defaultRunMode(visionConfigured) {
  return visionConfigured ? RUN_MODES.AI : RUN_MODES.OFFLINE;
}

/**
 * Clamp a caller-supplied adaptive maximum to a positive integer within
 * the engine hard cap. Non-numeric or out-of-range input falls back to
 * the UI default so the input can never enlarge a run by accident.
 */
export function clampAdaptiveMaxFrames(value) {
  const parsed =
    typeof value === 'number' ? value : Number(String(value ?? '').trim());
  if (!Number.isFinite(parsed)) {
    return ADAPTIVE_DEFAULT_MAX_FRAMES;
  }
  const floored = Math.floor(parsed);
  if (floored < 1) {
    return ADAPTIVE_DEFAULT_MAX_FRAMES;
  }
  return Math.min(floored, ADAPTIVE_HARD_MAX_FRAMES);
}

/**
 * Map a UI mode to worker options. AI mode always carries the explicit
 * frame limit; offline mode carries no limit key at all; adaptive mode
 * carries the validated maximum vision-request bound.
 */
export function buildProcessOptions(mode, { adaptiveMaxFrames } = {}) {
  if (isAdaptiveMode(mode)) {
    return {
      adaptive: true,
      adaptiveMaxFrames: clampAdaptiveMaxFrames(
        adaptiveMaxFrames ?? ADAPTIVE_DEFAULT_MAX_FRAMES,
      ),
    };
  }
  if (isAiMode(mode)) {
    return { skipVision: false, limit: VISION_FRAME_LIMIT };
  }
  return { skipVision: true };
}
