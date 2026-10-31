import { app, BrowserWindow, dialog, ipcMain, net, protocol } from 'electron';
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import started from 'electron-squirrel-startup';
import { createWorkerManager } from './python-worker.mjs';
import {
  createProject,
  deleteProject,
  getProjectOutputDir,
  getProject as getStoredProject,
  listArtifacts,
  listProjects,
  openProject,
  readArtifact,
  reconcileInterruptedRuns,
  resolveProjectMedia,
  settleProjectRun as settleStoredRun,
  updateProject,
} from './project-store.mjs';
import {
  buildWorkerArgv,
  isVisionConfigured,
  resolvePythonExecutable,
  resolveWorkerScript,
  validateVideoInput,
} from './worker-protocol.mjs';

// Handle creating/removing shortcuts on Windows when installing/uninstalling.
if (started) {
  app.quit();
}

// Custom project media scheme: standard + secure so <img>, <video>,
// and <audio> treat project:// URLs like https. Must register before
// the app is ready.
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'project',
    privileges: { standard: true, secure: true, supportFetchAPI: true },
  },
]);

// Repository root: <root>/electron/.vite/build in dev, <root>/electron/out
// in packaged builds. Worker and venv paths resolve from here so the app
// never depends on the process working directory.
function repoRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, '..');
  }
  return path.join(__dirname, '..', '..', '..');
}

const workerManager = createWorkerManager();
let mainWindow = null;
let activeProjectId = null;
let appQuitting = false;

// Main-process wrapper around the store backstop: never throws into
// worker lifecycle callbacks, even for deleted projects.
function settleProjectRun(projectId, status) {
  if (typeof projectId !== 'string' || projectId === '') {
    return;
  }
  try {
    settleStoredRun(app.getPath('userData'), projectId, status);
  } catch {
    // Project deleted or unreadable: nothing to reconcile.
  }
}

const createWindow = () => {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 650,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  // and load the index.html of the app.
  if (MAIN_WINDOW_VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(MAIN_WINDOW_VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(
      path.join(__dirname, `../renderer/${MAIN_WINDOW_VITE_NAME}/index.html`),
    );
  }
};

function sendWorkerEvent(message) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('worker-event', message);
  }
}


// Project-scoped media served over a privileged custom scheme so the
// renderer can render frame thumbnails and play audio without any
// filesystem access. Every segment is validated main-side (project id
// allow-list, fixed scopes, filename pattern, extension allow-list,
// containment, existence); anything else is a bare 404 with no detail.
// Tradeoff vs IPC data-URLs: this keeps full-resolution images and
// audio out of IPC payloads and memory, at the cost of one narrow
// protocol surface that only addresses files inside the user's own
// project directories.
function registerProjectMediaProtocol() {
  protocol.handle('project', (request) => {
    if (request.method !== 'GET') {
      return new Response('Not found', { status: 404 });
    }
    try {
      const url = new URL(request.url);
      const segments = url.pathname.split('/').filter(Boolean);
      // project://<id>/frames/<name> or project://<id>/audio/<scope>/<name>
      const [kind, ...rest] = segments;
      let scope = null;
      let name = null;
      if (kind === 'frames' && rest.length === 1) {
        scope = 'frames';
        [name] = rest;
      } else if (kind === 'audio' && rest.length === 2) {
        const [audioScope, audioName] = rest;
        if (audioScope !== 'audio' && audioScope !== 'commentary') {
          return new Response('Not found', { status: 404 });
        }
        scope = audioScope === 'audio' ? 'audio' : 'audio/commentary';
        name = audioName;
      } else {
        return new Response('Not found', { status: 404 });
      }
      const filePath = resolveProjectMedia(
        app.getPath('userData'),
        url.hostname,
        scope,
        name,
      );
      return net.fetch(pathToFileURL(filePath).toString());
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {
  try {
    reconcileInterruptedRuns(app.getPath('userData'));
  } catch {
    // userData unreadable: start anyway with no recovery.
  }
  registerProjectMediaProtocol();
  createWindow();

  // On OS X it's common to re-create a window in the app when the
  // dock icon is clicked and there are no other windows open.
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// Never leave a worker running after the app exits.
app.on('before-quit', () => {
  if (workerManager.isBusy()) {
    // Best effort: the process tree is torn down with the session, but an
    // explicit terminate avoids a stray paid API call or file write.
    appQuitting = true;
    void workerManager.cancel({ killGraceMs: 1000 });
  }
});

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
      const finishedProjectId = activeProjectId;
      activeProjectId = null;
      if (spawnError) {
        sendWorkerEvent({
          type: 'error',
          stage: 'spawn',
          message: `Could not launch the Python worker: ${spawnError.message}`,
        });
        settleProjectRun(finishedProjectId, 'error');
        return;
      }
      if (appQuitting) {
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
  activeProjectId = typeof opts.projectId === 'string' ? opts.projectId : null;
  return { ok: true, pid: startedJob.pid };
});

ipcMain.handle('list-projects', async () => {
  try {
    return { ok: true, projects: listProjects(app.getPath('userData')) };
  } catch (err) {
    return projectError(err);
  }
});

ipcMain.handle('create-project', async (_event, name, sourcePath) => {
  try {
    const project = createProject(app.getPath('userData'), { name, sourcePath });
    return { ok: true, project };
  } catch (err) {
    return projectError(err);
  }
});

ipcMain.handle('open-project', async (_event, id) => {
  try {
    const project = openProject(app.getPath('userData'), id);
    return { ok: true, project };
  } catch (err) {
    return projectError(err);
  }
});

ipcMain.handle('update-project', async (_event, id, patch) => {
  try {
    const project = updateProject(app.getPath('userData'), id, patch);
    return { ok: true, project };
  } catch (err) {
    return projectError(err);
  }
});

ipcMain.handle('delete-project', async (_event, id) => {
  try {
    if (workerManager.isBusy()) {
      return { ok: false, error: 'Stop the active processing job first.' };
    }
    return { ok: true, ...deleteProject(app.getPath('userData'), id) };
  } catch (err) {
    return projectError(err);
  }
});

ipcMain.handle('list-artifacts', async (_event, id) => {
  try {
    return {
      ok: true,
      artifacts: listArtifacts(app.getPath('userData'), id),
    };
  } catch (err) {
    return projectError(err);
  }
});

ipcMain.handle('read-artifact', async (_event, id, kind, name) => {
  try {
    return {
      ok: true,
      ...readArtifact(app.getPath('userData'), id, kind, name),
    };
  } catch (err) {
    return projectError(err);
  }
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
  if (!mainWindow || mainWindow.isDestroyed()) {
    return { ok: false, error: 'Application window is not available.' };
  }
  let selection;
  try {
    selection = await dialog.showOpenDialog(mainWindow, {
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

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and import them here.
