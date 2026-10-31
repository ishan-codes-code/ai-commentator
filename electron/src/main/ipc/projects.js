import { createProject, deleteProject, getProjectOutputDir, getProject as getStoredProject, listArtifacts, listProjects, openProject, readArtifact, settleProjectRun as settleStoredRun, updateProject } from '../../project-store.mjs';

function projectError(err) {
  return { ok: false, error: err?.message ?? 'Project operation failed.' };
}

export function registerProjectIpc({ ipcMain, app, workerManager }) {
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
  
  }
