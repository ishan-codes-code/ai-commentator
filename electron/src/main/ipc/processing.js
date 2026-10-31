import { getProjectOutputDir, getProject as getStoredProject, settleProjectRun as settleStoredRun } from '../../project-store.mjs';
import { buildWorkerArgv, isVisionConfigured, resolvePythonExecutable, resolveWorkerScript, validateVideoInput } from '../../worker-protocol.mjs';

export function registerProcessingIpc({ ipcMain, app, dialog, fs, workerManager, state, repoRoot, sendWorkerEvent, getMainWindow }) {
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
    if (!opts.skipVision && !isVisionConfigured()) {
      return {
        ok: false,
        error:
          'AI analysis needs a Baseten API key. Set BASETEN_API_KEY in ' +
          'the environment and restart the app, or use offline extraction.',
      };
    }
    let argv;
    try {
      argv = buildWorkerArgv(script, videoPath, opts);
    } catch (err) {
      return { ok: false, error: err?.message ?? 'Invalid run options.' };
    }
    const startedJob = workerManager.start({
      executable,
      argv,
      onMessage: (message) => sendWorkerEvent(message),
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
    // Boolean only: the key value never crosses IPC.
    try {
      return { ok: true, configured: isVisionConfigured() };
    } catch {
      return { ok: true, configured: false };
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
