import fs from 'node:fs';
import path from 'node:path';
import { DEFAULT_SETTINGS } from './settings-defaults.mjs';
export { DEFAULT_SETTINGS } from './settings-defaults.mjs';

export const SETTINGS_VERSION = 1;
const clone = (value) => JSON.parse(JSON.stringify(value));
const validText = (v, max) => typeof v === 'string' && v.length <= max;

export function validateSettings(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Settings must be an object.');
  if (value.version !== undefined && value.version !== SETTINGS_VERSION) throw new Error('Unsupported settings version.');
  const p = value.provider;
  const g = value.game;
  const c = value.prompt;
  if (!p || p.type !== 'openai-compatible') throw new Error('Unsupported provider type.');
  if (typeof p.baseUrl !== 'string' || p.baseUrl.length > 2048) throw new Error('Invalid provider URL.');
  let url;
  try { url = new URL(p.baseUrl); } catch { throw new Error('Provider URL must be a valid HTTPS URL.'); }
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.search || url.hash) throw new Error('Provider URL must be HTTPS and cannot contain credentials, query, or fragment.');
  if (!validText(p.model, 200) || p.model.trim() === '') throw new Error('Vision model is required.');
  if (typeof p.modelVisionVerified !== 'boolean') throw new Error('Vision model verification must be boolean.');
  if (!g || g.id !== 'free-fire' || !validText(g.context, 2000)) throw new Error('Unsupported game configuration.');
  if (!c || !validText(c.personality, 80) || !validText(c.tone, 80) || !validText(c.language, 80) || !validText(c.instructions, 2000)) throw new Error('Invalid prompt configuration.');
  if (typeof value.onboardingCompleted !== 'boolean') throw new Error('Invalid onboarding state.');
  return {
    version: SETTINGS_VERSION,
    provider: { type: p.type, baseUrl: url.toString().replace(/\/$/, ''), model: p.model.trim(), modelVisionVerified: p.modelVisionVerified },
    game: { id: g.id, context: g.context },
    prompt: { personality: c.personality, tone: c.tone, language: c.language, instructions: c.instructions },
    onboardingCompleted: value.onboardingCompleted,
  };
}

export function settingsPath(userDataPath) { return path.join(userDataPath, 'settings.json'); }

export function loadSettings(userDataPath) {
  const file = settingsPath(userDataPath);
  if (!fs.existsSync(file)) return clone(DEFAULT_SETTINGS);
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
    // Additive migration/defaulting keeps a partial prior file usable.
    return validateSettings({ ...clone(DEFAULT_SETTINGS), ...parsed,
      provider: { ...DEFAULT_SETTINGS.provider, ...(parsed.provider ?? {}) },
      game: { ...DEFAULT_SETTINGS.game, ...(parsed.game ?? {}) },
      prompt: { ...DEFAULT_SETTINGS.prompt, ...(parsed.prompt ?? {}) },
    });
  } catch {
    return clone(DEFAULT_SETTINGS);
  }
}

export function saveSettings(userDataPath, value) {
  const normalized = validateSettings(value);
  fs.mkdirSync(userDataPath, { recursive: true });
  const target = settingsPath(userDataPath);
  const temp = `${target}.${process.pid}.${Date.now()}.tmp`;
  try {
    fs.writeFileSync(temp, `${JSON.stringify(normalized, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    fs.renameSync(temp, target);
  } finally {
    if (fs.existsSync(temp)) fs.unlinkSync(temp);
  }
  return normalized;
}
