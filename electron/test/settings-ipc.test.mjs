import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { registerSettingsIpc } from '../src/main/ipc/settings.js';

test('credential IPC encrypts at rest and never returns key material', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-settings-ipc-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const handlers = {};
  const ipcMain = { handle: (name, fn) => { handlers[name] = fn; } };
  const safeStorage = { isEncryptionAvailable: () => true, encryptString: (v) => Buffer.from(`encrypted:${Buffer.from(v).toString('base64')}`), decryptString: (b) => Buffer.from(b.toString().slice('encrypted:'.length), 'base64').toString() };
  registerSettingsIpc({ ipcMain, app: { getPath: () => dir }, safeStorage, spawnSetup: async () => ({ ok: true, connected: true }) });
  const key = 'private-test-key';
  const stored = await handlers['credential-set']({}, key);
  assert.deepEqual(stored, { ok: true, configured: true });
  assert.equal(fs.readFileSync(path.join(dir, 'provider-key.enc'), 'utf8').includes(key), false);
  const loaded = await handlers['settings-load']();
  assert.equal(loaded.keyConfigured, true);
  assert.equal(JSON.stringify(loaded).includes(key), false);
});

test('secure storage unavailability fails closed', async () => {
  const handlers = {};
  registerSettingsIpc({ ipcMain: { handle: (name, fn) => { handlers[name] = fn; } }, app: { getPath: () => os.tmpdir() }, safeStorage: { isEncryptionAvailable: () => false }, spawnSetup: async () => ({ ok: true }) });
  const result = await handlers['credential-set']({}, 'private-key');
  assert.equal(result.ok, false);
  assert.equal(JSON.stringify(result).includes('private-key'), false);
});
