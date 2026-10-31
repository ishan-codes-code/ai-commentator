export function createMainWindow({ BrowserWindow, preloadPath, devServerUrl, rendererPath }) {
  const window = new BrowserWindow({
    width: 900,
    height: 650,
    webPreferences: {
      preload: preloadPath,
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  if (devServerUrl) {
    window.loadURL(devServerUrl);
  } else {
    window.loadFile(rendererPath);
  }
  return window;
}
