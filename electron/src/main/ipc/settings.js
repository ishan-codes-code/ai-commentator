import fs from 'node:fs';
import path from 'node:path';
import { loadSettings, saveSettings } from '../../settings-store.mjs';

export function registerSettingsIpc({ ipcMain, app, safeStorage, spawnSetup }) {
  const credentials = {
    available: () => safeStorage?.isEncryptionAvailable?.() === true,
    file: () => path.join(app.getPath('userData'), 'provider-key.enc'),
  };
  function writeKey(key) {
    if (!credentials.available()) throw new Error('Secure credential storage is unavailable on this device.');
    if (typeof key !== 'string' || !key.trim() || key.length > 4096) throw new Error('API key must be a non-empty string under 4096 characters.');
    const file = credentials.file();
    const temp = `${file}.${process.pid}.tmp`;
    try { fs.writeFileSync(temp, safeStorage.encryptString(key), { flag: 'w', mode: 0o600 }); fs.renameSync(temp, file); }
    finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
  }
  function readKey() {
    if (!credentials.available()) throw new Error('Secure credential storage is unavailable on this device.');
    const file = credentials.file();
    return fs.existsSync(file) ? safeStorage.decryptString(fs.readFileSync(file)) : '';
  }
  const safe = (err) => ({ ok: false, error: (err?.message ?? 'Settings operation failed.').replace(/.*/s, 'Settings operation failed.') });
  ipcMain.handle('settings-load', async () => {
    try { const settings = loadSettings(app.getPath('userData')); return { ok: true, settings, credentialAvailable: credentials.available(), keyConfigured: credentials.available() && fs.existsSync(credentials.file()) }; }
    catch (err) { return safe(err); }
  });
  ipcMain.handle('settings-save', async (_event, value) => {
    try {
      const current = loadSettings(app.getPath('userData'));
      const requested = { ...value, provider: { ...value?.provider,
        modelVisionVerified: value?.provider?.baseUrl === current.provider.baseUrl && value?.provider?.model === current.provider.model
          ? current.provider.modelVisionVerified : false } };
      return { ok: true, settings: saveSettings(app.getPath('userData'), requested) };
    } catch (err) { return safe(err); }
  });
  ipcMain.handle('credential-set', async (_event, key) => { try { writeKey(key); return { ok: true, configured: true }; } catch (err) { return safe(err); } });
  ipcMain.handle('credential-delete', async () => { try { const file = credentials.file(); if (fs.existsSync(file)) fs.unlinkSync(file); return { ok: true, configured: false }; } catch (err) { return safe(err); } });
  ipcMain.handle('provider-setup', async (_event, operation, settings) => {
    try {
      if (!['connection', 'models', 'verify-vision'].includes(operation)) throw new Error('Unsupported provider operation.');
      const saved = loadSettings(app.getPath('userData'));
      const candidate = saveSettings(app.getPath('userData'), { ...saved, ...settings, provider: { ...saved.provider, ...(settings?.provider ?? {}), modelVisionVerified: settings?.provider?.baseUrl === saved.provider.baseUrl && settings?.provider?.model === saved.provider.model ? saved.provider.modelVisionVerified : false } });
      const key = readKey();
      if (!key) throw new Error('Save an API key before testing the provider.');
      const result = await spawnSetup({ operation, provider: candidate.provider, key });
      if (operation === 'verify-vision' && result?.ok === true) {
        const verified = { ...candidate, provider: { ...candidate.provider, modelVisionVerified: true } };
        return { ...result, settings: saveSettings(app.getPath('userData'), verified) };
      }
      return result;
    } catch (err) { return safe(err); }
  });
}
