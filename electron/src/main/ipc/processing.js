import path from 'node:path';
import { getProjectOutputDir, getProject as getStoredProject, settleProjectRun as settleStoredRun } from '../../project-store.mjs';
import { buildWorkerArgv, isVisionConfigured, resolvePythonExecutable, resolveWorkerScript, validateVideoInput } from '../../worker-protocol.mjs';
import { loadSettings } from '../../settings-store.mjs';

export function registerProcessingIpc({ ipcMain, app, dialog, fs, safeStorage, workerManager, state, repoRoot, sendWorkerEvent, getMainWindow }) {
  // True once the active run emitted its terminal `stopped` message.
  // Reset on every start; runs never overlap, so one flag is enough.
  let stoppedSeenForActiveRun = false;
  function settleProjectRun(projectId, status) {
    if (typeof projectId !== 'string' || projectId === '') return;
    try { settleStoredRun(app.getPath('userData'), projectId, status); } catch { /* Project deleted or unreadable. */ }
  }

  function bindProjectPaths(projectId) {
    let project = null;
    try {
      project =
        typeof projectId === 'string'
          ? getStoredProject(app.getPath('userData'), projectId)
          : null;
    } catch {
      project = null;
    }
    if (!project) {
      return { error: 'Project not found.' };
    }
    const inputError = validateVideoInput(project.sourceVideo.path);
    if (inputError) {
      return { error: inputError };
    }
    return {
      videoPath: project.sourceVideo.path,
      outputDir: getProjectOutputDir(app.getPath('userData'), projectId),
    };
  }
  
  function projectError(err) {
    return { ok: false, error: err?.message ?? 'Project operation failed.' };
  }
  
  ipcMain.handle('process-video', async (_event, videoPath, options = {}) => {
    const inputError = validateVideoInput(videoPath);
    if (inputError) {
      return { ok: false, error: inputError };
    }
    const opts = options && typeof options === 'object' ? options : {};
    if (workerManager.isBusy()) {
      return { ok: false, error: 'A video is already being processed.' };
    }
    // Project-bound runs ignore renderer-supplied paths: the source
    // video and output directory resolve from stored project metadata,
    // keeping per-project artifacts isolated by construction.
    if (opts.projectId !== undefined) {
      const bound = bindProjectPaths(opts.projectId);
      if (bound.error) {
        return { ok: false, error: bound.error };
      }
      videoPath = bound.videoPath;
      opts.outputDir = bound.outputDir;
    }
    const root = repoRoot();
    const { executable, missing } = resolvePythonExecutable(root);
    if (!executable) {
      return {
        ok: false,
        error:
          `Python virtual environment not found at ${missing}. ` +
          'Create it with `python3 -m venv .venv` and install requirements.',
      };
    }
    const { script, missing: missingScript } = resolveWorkerScript(root);
    if (!script) {
      return { ok: false, error: `Worker script not found at ${missingScript}.` };
    }
    // AI analysis is a paid run: refuse to spawn without a key rather
    // than silently falling back to offline mode. The worker also
    // fails fast, but this stops the job before any extraction work.
    let userSettings;
    try { userSettings = loadSettings(app.getPath('userData')); } catch { userSettings = null; }
    const hasStoredCredential = (() => { try { return fs.existsSync(path.join(app.getPath('userData'), 'provider-key.enc')); } catch { return false; } })();
    const legacyCredential = typeof process.env.BASETEN_API_KEY === 'string' ? process.env.BASETEN_API_KEY : '';
    const paidRequested = !opts.skipVision || opts.withScript || opts.adaptive;
    if (paidRequested && !hasStoredCredential && !legacyCredential) {
      return {
        ok: false,
        error: 'AI analysis needs an API key. Open Settings to configure a provider, or choose offline extraction.',
      };
    }
    let argv;
    try {
      argv = buildWorkerArgv(script, videoPath, opts);
    } catch (err) {
      return { ok: false, error: err?.message ?? 'Invalid run options.' };
    }
    stoppedSeenForActiveRun = false;
    let credential = '';
    try {
      if (hasStoredCredential && safeStorage?.isEncryptionAvailable?.()) {
        credential = safeStorage.decryptString(fs.readFileSync(path.join(app.getPath('userData'), 'provider-key.enc')));
      }
    } catch { credential = ''; }
    if (!credential) credential = legacyCredential;
    if (paidRequested && !credential) {
      return { ok: false, error: 'Secure API credential is unavailable. Check Settings and secure storage.' };
    }
    if (paidRequested && hasStoredCredential && userSettings && !userSettings.provider.modelVisionVerified) {
      return { ok: false, error: 'The selected model has not been verified for image input. Open Settings and run Verify image support.' };
    }
    const workerEnv = { ...process.env };
    delete workerEnv.BASETEN_API_KEY;
    delete workerEnv.AI_COMMENTATOR_PROVIDER_API_KEY;
    if (credential) workerEnv.BASETEN_API_KEY = credential;
    if (userSettings) {
      workerEnv.BASETEN_BASE_URL = userSettings.provider.baseUrl;
      workerEnv.BASETEN_MODEL = userSettings.provider.model;
      workerEnv.AI_COMMENTATOR_SETTINGS_JSON = JSON.stringify(userSettings);
    }
    const startedJob = workerManager.start({
      executable,
      argv,
      env: workerEnv,
      onMessage: (message) => {
        if (message && message.type === 'stopped') {
          stoppedSeenForActiveRun = true;
        }
        sendWorkerEvent(message);
      },
      onStderr: (text) => sendWorkerEvent({ type: 'worker-log', text }),
      onExit: ({ code, signal, cancelled, spawnError }) => {
        const finishedProjectId = state.activeProjectId;
        state.activeProjectId = null;
        if (spawnError) {
          sendWorkerEvent({
            type: 'error',
            stage: 'spawn',
            message: `Could not launch the Python worker: ${spawnError.message}`,
          });
          settleProjectRun(finishedProjectId, 'error');
          return;
        }
        if (state.appQuitting) {
          settleProjectRun(finishedProjectId, 'interrupted');
        } else if (stoppedSeenForActiveRun) {
          settleProjectRun(finishedProjectId, 'stopped');
        } else if (cancelled) {
          settleProjectRun(finishedProjectId, 'cancelled');
        } else if (code === 0) {
          settleProjectRun(finishedProjectId, 'done');
        } else {
          settleProjectRun(finishedProjectId, 'error');
        }
        sendWorkerEvent({
          type: 'worker-exit',
          code,
          signal,
          cancelled: Boolean(cancelled),
        });
      },
    });
    if (!startedJob.started) {
      return { ok: false, error: 'A video is already being processed.' };
    }
    state.activeProjectId = typeof opts.projectId === 'string' ? opts.projectId : null;
    return { ok: true, pid: startedJob.pid };
  });
  
  ipcMain.handle('vision-status', async () => {
    // Safe readiness metadata only: credential bytes never cross IPC.
    try {
      const stored = fs.existsSync(path.join(app.getPath('userData'), 'provider-key.enc'));
      const current = loadSettings(app.getPath('userData'));
      const keyConfigured = stored || isVisionConfigured();
      const secureReady = !stored || (safeStorage?.isEncryptionAvailable?.() === true && current.provider.modelVisionVerified);
      const ready = keyConfigured && secureReady;
      return { ok: true, configured: ready, ready, keyConfigured, missing: !keyConfigured ? 'API key' : stored && safeStorage?.isEncryptionAvailable?.() !== true ? 'secure storage' : stored && !current.provider.modelVisionVerified ? 'verified vision model' : null };
    } catch {
      return { ok: true, configured: false };
    }
  });
  
  ipcMain.handle('request-stop', async () => {
    // Cooperative stop: one control line over the worker's stdin. The
    // renderer learns only whether the request was sent; completion
    // arrives as worker events (`stopped`, then `worker-exit`).
    try {
      const result = workerManager.requestStop();
      if (!result || typeof result !== 'object') {
        return { ok: false, error: 'Stop request failed.' };
      }
      return {
        ok: true,
        requested: result.requested === true,
        reason: typeof result.reason === 'string' ? result.reason : 'unknown',
      };
    } catch {
      return { ok: false, error: 'Stop request failed.' };
    }
  });

  ipcMain.handle('cancel-processing', async () => {
    const stopped = await workerManager.cancel();
    return { ok: true, stopped };
  });
  
  ipcMain.handle('select-video', async () => {
    if (!getMainWindow() || getMainWindow().isDestroyed()) {
      return { ok: false, error: 'Application window is not available.' };
    }
    let selection;
    try {
      selection = await dialog.showOpenDialog(getMainWindow(), {
        title: 'Select gameplay video',
        properties: ['openFile'],
        filters: [
          {
            name: 'Videos',
            extensions: ['mp4', 'avi', 'mov', 'mkv', 'webm'],
          },
        ],
      });
    } catch {
      return { ok: false, error: 'Could not open the file dialog.' };
    }
    if (selection.canceled || selection.filePaths.length === 0) {
      return { ok: true, cancelled: true };
    }
    const filePath = selection.filePaths[0];
    const inputError = validateVideoInput(filePath);
    if (inputError) {
      return { ok: false, error: inputError };
    }
    let sizeBytes = null;
    try {
      const stat = fs.statSync(filePath);
      if (stat.isFile()) {
        sizeBytes = stat.size;
      }
    } catch {
      sizeBytes = null;
    }
    if (sizeBytes === null) {
      return { ok: false, error: 'The selected file could not be read.' };
    }
    return { ok: true, path: filePath, sizeBytes };
  });
  
  }
