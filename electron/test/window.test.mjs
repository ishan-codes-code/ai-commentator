import test from 'node:test';
import assert from 'node:assert/strict';
import { createMainWindow } from '../src/main/window.js';

function makeWindowFactory() {
  const calls = { options: null, maximized: 0, loaded: null };
  const window = {
    maximize() { calls.maximized += 1; },
    loadURL(url) { calls.loaded = ['url', url]; },
    loadFile(file) { calls.loaded = ['file', file]; },
  };
  class BrowserWindow {
    constructor(options) {
      calls.options = options;
      return window;
    }
  }
  return { calls, BrowserWindow };
}

test('creates a secure window at the original 900 by 650 size', () => {
  const { calls, BrowserWindow } = makeWindowFactory();
  const window = createMainWindow({ BrowserWindow, preloadPath: '/preload.js', rendererPath: '/index.html' });

  assert.ok(window);
  assert.equal(calls.options.width, 900);
  assert.equal(calls.options.height, 650);
  assert.equal(calls.options.webPreferences.nodeIntegration, false);
  assert.equal(calls.options.webPreferences.contextIsolation, true);
  assert.equal(calls.maximized, 0);
});

test('preserves the selected renderer loading path', () => {
  const dev = makeWindowFactory();
  createMainWindow({ BrowserWindow: dev.BrowserWindow, preloadPath: '/preload.js', devServerUrl: 'http://localhost:5173', rendererPath: '/index.html' });
  assert.deepEqual(dev.calls.loaded, ['url', 'http://localhost:5173']);

  const packaged = makeWindowFactory();
  createMainWindow({ BrowserWindow: packaged.BrowserWindow, preloadPath: '/preload.js', rendererPath: '/index.html' });
  assert.deepEqual(packaged.calls.loaded, ['file', '/index.html']);
});
