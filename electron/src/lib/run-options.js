/**
 * Pure run-mode mapping for the workspace processing selector.
 *
 * AI analysis runs vision on exactly VISION_FRAME_LIMIT frames; offline
 * extraction skips vision. No IPC, no filesystem, no secrets here — the
 * main process validates and enforces these options before spawning.
 */

export const VISION_FRAME_LIMIT = 10;

export const RUN_MODES = {
  AI: 'ai',
  OFFLINE: 'offline',
};

export function isAiMode(mode) {
  return mode === RUN_MODES.AI;
}

/** Default to AI analysis only when a key is configured. */
export function defaultRunMode(visionConfigured) {
  return visionConfigured ? RUN_MODES.AI : RUN_MODES.OFFLINE;
}

/**
 * Map a UI mode to worker options. AI mode always carries the explicit
 * frame limit; offline mode carries no limit key at all.
 */
export function buildProcessOptions(mode) {
  if (isAiMode(mode)) {
    return { skipVision: false, limit: VISION_FRAME_LIMIT };
  }
  return { skipVision: true };
}
