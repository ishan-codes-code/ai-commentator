import { app, BrowserWindow, dialog, ipcMain, net, protocol, safeStorage } from 'electron';
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import started from 'electron-squirrel-startup';
import { createWorkerManager } from './python-worker.mjs';
import { resolvePythonExecutable } from './worker-protocol.mjs';
import { registerProjectIpc } from './main/ipc/projects.js';
import { registerProcessingIpc } from './main/ipc/processing.js';
import { registerSettingsIpc } from './main/ipc/settings.js';
import { registerProjectMediaProtocol } from './main/media-protocol.js';
import { createMainWindow } from './main/window.js';
import { reconcileInterruptedRuns, resolveProjectMedia } from './project-store.mjs';

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
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
    },
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
const processingState = { activeProjectId: null, appQuitting: false };

function spawnSetup({ operation, provider, key }) {
  const root = repoRoot();
  const { executable } = resolvePythonExecutable(root);
  if (!executable) return Promise.resolve({ ok: false, error: 'Python virtual environment is not configured.' });
  return new Promise((resolve) => {
    const child = spawn(executable, [path.join(root, 'python', 'provider_setup.py'), operation,
      '--base-url', provider.baseUrl, '--model', provider.model], {
      stdio: ['ignore', 'pipe', 'ignore'],
      env: { PATH: process.env.PATH ?? '', SYSTEMROOT: process.env.SYSTEMROOT ?? '',
        AI_COMMENTATOR_PROVIDER_API_KEY: key },
    });
    let output = '';
    let settled = false;
    const finish = (result) => { if (!settled) { settled = true; resolve(result); } };
    const timeout = setTimeout(() => { child.kill(); finish({ ok: false, error: 'Provider request timed out.' }); }, 25000);
    child.stdout.on('data', (chunk) => { output += String(chunk).slice(0, 32768); });
    child.on('error', () => { clearTimeout(timeout); finish({ ok: false, error: 'Could not start provider setup check.' }); });
    child.on('close', () => {
      clearTimeout(timeout);
      try {
        const result = JSON.parse(output.trim().split(/\r?\n/).at(-1));
        if (!result || typeof result !== 'object' || result.ok !== true) return finish({ ok: false, error: 'Provider request failed. Check the URL, key, model, and provider compatibility.' });
        finish(result);
      } catch { finish({ ok: false, error: 'Provider returned an invalid setup response.' }); }
    });
  });
}

function createWindow() {
  mainWindow = createMainWindow({
    BrowserWindow,
    preloadPath: path.join(__dirname, 'preload.js'),
    devServerUrl: MAIN_WINDOW_VITE_DEV_SERVER_URL,
    rendererPath: path.join(__dirname, '../renderer', `${MAIN_WINDOW_VITE_NAME}/index.html`),
  });
}

function sendWorkerEvent(message) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('worker-event', message);
  }
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
  registerProjectMediaProtocol({ protocol, net, pathToFileURL, app, resolveProjectMedia });
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
    processingState.appQuitting = true;
    void workerManager.cancel({ killGraceMs: 1000 });
  }
});

registerProcessingIpc({ ipcMain, app, dialog, fs, safeStorage, workerManager, state: processingState, repoRoot, sendWorkerEvent, getMainWindow: () => mainWindow });

registerProjectIpc({ ipcMain, app, workerManager });
registerSettingsIpc({ ipcMain, app, safeStorage, spawnSetup });

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and import them here.
