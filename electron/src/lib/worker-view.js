/**
 * Pure mapping from worker-protocol messages to run view state.
 *
 * The worker speaks NDJSON (`started`, `progress`, `stage_completed`,
 * `result`, `error`; see `docs/contracts/worker-protocol.md`) plus the
 * two renderer-synthesized messages (`worker-log`, `worker-exit`).
 * This module turns that stream into renderable state with no side
 * effects: no IPC, no timers, no mutation of its inputs.
 */

export const STAGE_ORDER = [
  'frame_extraction',
  'vision_analysis',
  'state_rebuild',
  'event_detection',
  'commentary',
  'script',
];

export const STAGE_LABELS = {
  frame_extraction: 'Frame extraction',
  vision_analysis: 'Vision analysis',
  state_rebuild: 'Match-state reconstruction',
  event_detection: 'Event detection',
  commentary: 'Commentary generation',
  script: 'Script generation',
};

export function stageLabel(stage) {
  return STAGE_LABELS[stage] ?? stage;
}

export const SKIP_REASONS = {
  'option-skipped': 'Skipped by run options',
  'no-observations': 'No observations available',
};

export function skipReasonLabel(reason) {
  if (!reason) {
    return '';
  }
  return SKIP_REASONS[reason] ?? reason;
}

const DETAIL_METRICS = [
  ['frames', 'frames'],
  ['observations', 'observations'],
  ['events', 'events'],
  ['lines', 'lines'],
];

/**
 * One-line honest summary of a stage_completed detail object, using only
 * real counts the worker reported (e.g. "10 observations"). Returns an
 * empty string when there is nothing countable to show.
 */
export function stageDetailText(detail) {
  if (!detail || typeof detail !== 'object') {
    return '';
  }
  if (detail.skipped === true) {
    return skipReasonLabel(detail.reason);
  }
  const parts = [];
  for (const [key, label] of DETAIL_METRICS) {
    const value = detail[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      parts.push(`${value} ${label}`);
    }
  }
  return parts.join(' · ');
}

export const MAX_WORKER_LOGS = 50;

export function createInitialRunState() {
  return {
    status: 'idle',
    currentStage: null,
    completedCount: 0,
    totalStages: 0,
    stages: {},
    result: null,
    error: null,
    workerLogs: [],
    // True once the worker lifecycle for this run is over (worker-exit,
    // or a spawn failure that never produces a process). The UI only
    // allows a new run after the previous run has settled, so a late
    // exit from an old run can never corrupt a new session.
    settled: true,
  };
}

function appendLog(state, text) {
  const workerLogs = [...state.workerLogs, text].slice(-MAX_WORKER_LOGS);
  return { ...state, workerLogs };
}

/**
 * Fold one worker message into run state. Unknown or malformed messages
 * are ignored so a single bad line can never corrupt a session.
 */
export function applyWorkerMessage(state, message) {
  if (!message || typeof message.type !== 'string') {
    return state;
  }
  switch (message.type) {
    case 'started': {
      const fresh = createInitialRunState();
      fresh.status = 'processing';
      fresh.settled = false;
      fresh.detail = message.message ?? 'Processing video…';
      return fresh;
    }
    case 'progress': {
      return {
        ...state,
        status: state.status === 'idle' ? 'processing' : state.status,
        currentStage: message.stage ?? state.currentStage,
        completedCount:
          typeof message.completed === 'number'
            ? message.completed
            : state.completedCount,
        totalStages:
          typeof message.total === 'number'
            ? message.total
            : state.totalStages,
      };
    }
    case 'stage_completed': {
      const stage = message.stage;
      if (!stage) {
        return state;
      }
      const skipped = message.detail?.skipped === true;
      return {
        ...state,
        stages: {
          ...state.stages,
          [stage]: {
            status: skipped ? 'skipped' : 'done',
            detail: message.detail ?? null,
          },
        },
        currentStage: null,
      };
    }
    case 'result': {
      return {
        ...state,
        status: 'completed',
        currentStage: null,
        result: message.data ?? null,
        error: null,
      };
    }
    case 'error': {
      return {
        ...state,
        status: 'failed',
        currentStage: null,
        settled: message.stage === 'spawn' ? true : state.settled,
        error: {
          stage: message.stage ?? 'worker',
          message: message.message ?? 'Processing failed.',
        },
      };
    }
    case 'worker-log': {
      if (typeof message.text !== 'string' || message.text === '') {
        return state;
      }
      return appendLog(state, message.text);
    }
    case 'worker-exit': {
      if (message.cancelled) {
        return { ...state, status: 'cancelled', currentStage: null, settled: true };
      }
      if (state.status === 'completed' || state.status === 'failed') {
        return { ...state, settled: true };
      }
      return {
        ...state,
        status: 'failed',
        currentStage: null,
        settled: true,
        error: {
          stage: 'worker',
          message: `Worker exited with code ${message.code ?? '?'}.`,
        },
      };
    }
    default:
      return state;
  }
}
