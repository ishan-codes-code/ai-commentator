import { contextBridge, ipcRenderer } from 'electron';

// Minimal, audited bridge between the renderer and the main process.
// The renderer never sees Node.js, the Python interpreter, or API keys:
// it can only start/cancel a processing job and listen to worker events.
contextBridge.exposeInMainWorld('api', {
  processVideo: (videoPath, options) =>
    ipcRenderer.invoke('process-video', videoPath, options ?? {}),
  cancelProcessing: () => ipcRenderer.invoke('cancel-processing'),
  selectVideo: () => ipcRenderer.invoke('select-video'),
  listProjects: () => ipcRenderer.invoke('list-projects'),
  createProject: (name, sourcePath) =>
    ipcRenderer.invoke('create-project', name, sourcePath),
  openProject: (id) => ipcRenderer.invoke('open-project', id),
  updateProject: (id, patch) => ipcRenderer.invoke('update-project', id, patch),
  deleteProject: (id) => ipcRenderer.invoke('delete-project', id),
  onWorkerEvent: (callback) => {
    if (typeof callback !== 'function') {
      throw new TypeError('callback must be a function.');
    }
    const listener = (_event, message) => callback(message);
    ipcRenderer.on('worker-event', listener);
    return () => ipcRenderer.removeListener('worker-event', listener);
  },
});
