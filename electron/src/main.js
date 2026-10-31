import { app, BrowserWindow, dialog, ipcMain } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import started from 'electron-squirrel-startup';
import { createWorkerManager } from './python-worker.mjs';
import {
  resolvePythonExecutable,
  resolveWorkerScript,
  validateVideoInput,
} from './worker-protocol.mjs';

// Handle creating/removing shortcuts on Windows when installing/uninstalling.
if (started) {
  app.quit();
}

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

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {
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
    void workerManager.cancel({ killGraceMs: 1000 });
  }
});

ipcMain.handle('process-video', async (_event, videoPath, options = {}) => {
  const inputError = validateVideoInput(videoPath);
  if (inputError) {
    return { ok: false, error: inputError };
  }
  const opts = options && typeof options === 'object' ? options : {};
  if (workerManager.isBusy()) {
    return { ok: false, error: 'A video is already being processed.' };
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
  const argv = [script, videoPath];
  if (opts.outputDir) {
    argv.push('--output-dir', String(opts.outputDir));
  }
  if (opts.limit !== undefined) {
    argv.push('--limit', String(opts.limit));
  }
  if (opts.jobs !== undefined) {
    argv.push('--jobs', String(opts.jobs));
  }
  if (opts.force) {
    argv.push('--force');
  }
  if (opts.skipVision) {
    argv.push('--skip-vision');
  }
  if (opts.withScript) {
    argv.push('--with-script');
  }
  const startedJob = workerManager.start({
    executable,
    argv,
    onMessage: (message) => sendWorkerEvent(message),
    onStderr: (text) => sendWorkerEvent({ type: 'worker-log', text }),
    onExit: ({ code, signal, cancelled, spawnError }) => {
      if (spawnError) {
        sendWorkerEvent({
          type: 'error',
          stage: 'spawn',
          message: `Could not launch the Python worker: ${spawnError.message}`,
        });
        return;
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
  return { ok: true, pid: startedJob.pid };
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
