import fs from 'node:fs';
import path from 'node:path';
import { getProjectsRoot, resolveProjectDir } from './metadata.mjs';

export const MAX_ARTIFACT_FILE_BYTES = 1024 * 1024;
export const MAX_LIST_ENTRIES = 200;

const FRAME_RE = /^frame_(\d+)\.(jpg|jpeg|png)$/;
const OBSERVATION_RE = /^frame_(\d+)\.json$/;
const AUDIO_EXTENSIONS = new Set(['.mp3', '.wav', '.ogg', '.m4a']);

const ARTIFACT_FILES = {
  state: ['observations', 'match_state.json'],
  events: ['events', 'events.json'],
  commentary: ['commentary', 'commentary.json'],
  script: ['script', 'script.json'],
};

function projectSubdir(projectDir, ...parts) {
  const resolved = path.join(projectDir, ...parts);
  const relative = path.relative(projectDir, resolved);
  if (relative === '' || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error('Invalid artifact path.');
  }
  return resolved;
}

function readdirEntries(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true });
  } catch (err) {
    if (err?.code === 'ENOENT' || err?.code === 'ENOTDIR') {
      return [];
    }
    throw err;
  }
}

function readJsonDocument(filePath) {
  let stat = null;
  try {
    stat = fs.statSync(filePath);
  } catch {
    stat = null;
  }
  if (!stat || !stat.isFile()) {
    throw new Error('Artifact not found.');
  }
  if (stat.size > MAX_ARTIFACT_FILE_BYTES) {
    throw new Error('Artifact is too large to preview.');
  }
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    throw new Error('Artifact is not valid JSON.');
  }
  return parsed;
}

function summarizeJsonDocument(filePath) {
  try {
    readJsonDocument(filePath);
    return { present: true, invalid: false };
  } catch (err) {
    if (err?.message === 'Artifact not found.') {
      return { present: false, invalid: false };
    }
    return { present: true, invalid: true };
  }
}

/** List a project's artifacts: counts plus capped, validated entries. */
export function listArtifacts(userDataPath, projectId) {
  const root = getProjectsRoot(userDataPath);
  const dir = resolveProjectDir(root, projectId);
  if (!fs.existsSync(path.join(dir, 'project.json'))) {
    throw new Error('Project not found.');
  }
  const outputDir = path.join(dir, 'output');

  const frameEntries = [];
  let frameCount = 0;
  for (const entry of readdirEntries(path.join(outputDir, 'frames'))) {
    if (!entry.isFile()) continue;
    const match = FRAME_RE.exec(entry.name);
    if (!match) continue;
    frameCount += 1;
    if (frameEntries.length < MAX_LIST_ENTRIES) {
      frameEntries.push({ name: entry.name, timestamp: Number(match[1]) });
    }
  }
  frameEntries.sort((a, b) => a.timestamp - b.timestamp);

  const observationEntries = [];
  let observationCount = 0;
  for (const entry of readdirEntries(path.join(outputDir, 'observations'))) {
    if (!entry.isFile()) continue;
    const match = OBSERVATION_RE.exec(entry.name);
    if (!match || entry.name === 'match_state.json') continue;
    observationCount += 1;
    if (observationEntries.length < MAX_LIST_ENTRIES) {
      observationEntries.push({ name: entry.name, timestamp: Number(match[1]) });
    }
  }
  observationEntries.sort((a, b) => a.timestamp - b.timestamp);

  const state = summarizeJsonDocument(projectSubdir(dir, 'output', ...ARTIFACT_FILES.state));
  const eventsSummary = summarizeJsonDocument(projectSubdir(dir, 'output', ...ARTIFACT_FILES.events));
  const commentarySummary = summarizeJsonDocument(projectSubdir(dir, 'output', ...ARTIFACT_FILES.commentary));
  const scriptSummary = summarizeJsonDocument(projectSubdir(dir, 'output', ...ARTIFACT_FILES.script));

  let eventCount = 0;
  if (eventsSummary.present && !eventsSummary.invalid) {
    const doc = readJsonDocument(projectSubdir(dir, 'output', ...ARTIFACT_FILES.events));
    if (Array.isArray(doc.events)) eventCount = doc.events.length;
  }
  let commentaryLines = 0;
  if (commentarySummary.present && !commentarySummary.invalid) {
    const doc = readJsonDocument(projectSubdir(dir, 'output', ...ARTIFACT_FILES.commentary));
    if (Array.isArray(doc.lines)) commentaryLines = doc.lines.length;
  }
  let scriptStatus = null;
  if (scriptSummary.present && !scriptSummary.invalid) {
    const doc = readJsonDocument(projectSubdir(dir, 'output', ...ARTIFACT_FILES.script));
    if (doc && typeof doc.status !== 'undefined') scriptStatus = String(doc.status);
  }

  const audioFiles = [];
  for (const scope of ['audio', 'audio/commentary']) {
    const scopeDir = scope === 'audio' ? [] : ['commentary'];
    for (const entry of readdirEntries(path.join(dir, 'audio', ...scopeDir))) {
      if (!entry.isFile()) continue;
      if (!AUDIO_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) continue;
      let sizeBytes = 0;
      try {
        sizeBytes = fs.statSync(path.join(dir, 'audio', ...scopeDir, entry.name)).size;
      } catch {
        continue;
      }
      audioFiles.push({ name: entry.name, scope, sizeBytes });
    }
  }

  return {
    projectId,
    frames: { count: frameCount, items: frameEntries },
    observations: { count: observationCount, items: observationEntries },
    state,
    events: { ...eventsSummary, count: eventCount },
    commentary: { ...commentarySummary, lines: commentaryLines },
    script: { ...scriptSummary, status: scriptStatus },
    audio: { files: audioFiles },
  };
}

/**
 * Read one JSON artifact by kind. `name` is required for observations
 * and must equal the fixed filename for the other kinds. Anything else
 * (traversal, unexpected types, oversized or corrupt files) throws.
 */
export function readArtifact(userDataPath, projectId, kind, name) {
  const root = getProjectsRoot(userDataPath);
  const dir = resolveProjectDir(root, projectId);
  if (!fs.existsSync(path.join(dir, 'project.json'))) {
    throw new Error('Project not found.');
  }
  let filePath;
  if (kind === 'observation') {
    if (typeof name !== 'string' || !OBSERVATION_RE.test(name)) {
      throw new Error('Invalid artifact name.');
    }
    filePath = projectSubdir(dir, 'output', 'observations', name);
  } else if (Object.prototype.hasOwnProperty.call(ARTIFACT_FILES, kind)) {
    const [subdir, filename] = ARTIFACT_FILES[kind];
    if (name !== undefined && name !== null && name !== filename) {
      throw new Error('Invalid artifact name.');
    }
    filePath = projectSubdir(dir, 'output', subdir, filename);
  } else {
    throw new Error('Unsupported artifact kind.');
  }
  return { kind, name: path.basename(filePath), data: readJsonDocument(filePath) };
}

