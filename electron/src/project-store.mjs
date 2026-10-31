import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { validateVideoInput } from './worker-protocol.mjs';

/**
 * Project storage for the AI Commentator desktop app.
 *
 * Layout (under Electron `userData`, never the repository):
 *
 *   projects/
 *     <project-id>/
 *       project.json
 *       source/gameplay.<ext>
 *       output/frames|observations|events|commentary/
 *       audio/commentary/
 *
 * All functions take an explicit `userDataPath` so tests can point at a
 * temporary root. Every id is allow-listed and every resolved path is
 * containment-checked against the projects root: renderer input can
 * never escape it. Metadata writes are atomic (temp file + rename) and
 * validated on load; failed imports remove their staging directory so
 * no partially-created project is ever left behind.
 */

export const SCHEMA_VERSION = 1;
export const PROJECTS_DIR_NAME = 'projects';

const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const MAX_NAME_LENGTH = 100;
const PATCHABLE_FIELDS = new Set(['processingStatus', 'lastOpenedAt']);
const PROCESSING_STATUSES = new Set([
  'idle',
  'processing',
  'done',
  'error',
  'cancelled',
  // Terminal state written only by startup recovery (or shutdown) when a
  // recorded 'processing' run has no live worker behind it. Never written
  // for completed, failed, or cancelled runs, and never successful.
  'interrupted',
]);

export function getProjectsRoot(userDataPath) {
  return path.join(userDataPath, PROJECTS_DIR_NAME);
}

function assertId(id) {
  if (typeof id !== 'string' || !ID_PATTERN.test(id)) {
    throw new Error('Invalid project id.');
  }
}

function resolveProjectDir(projectsRoot, id) {
  assertId(id);
  const dir = path.join(projectsRoot, id);
  const relative = path.relative(projectsRoot, dir);
  if (relative === '' || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error('Invalid project id.');
  }
  return dir;
}

function slugify(name) {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
  return slug || 'project';
}

function validateName(name) {
  if (typeof name !== 'string') {
    throw new Error('Project name must be a string.');
  }
  const trimmed = name.trim();
  if (trimmed === '') {
    throw new Error('Project name must not be empty.');
  }
  if (trimmed.length > MAX_NAME_LENGTH) {
    throw new Error('Project name is too long.');
  }
  return trimmed;
}

function validateMetadata(value) {
  if (!value || typeof value !== 'object') {
    throw new Error('Invalid project metadata.');
  }
  assertId(value.id);
  validateName(value.name);
  if (value.schemaVersion !== SCHEMA_VERSION) {
    throw new Error('Unsupported project version.');
  }
  for (const key of ['createdAt', 'updatedAt', 'lastOpenedAt']) {
    if (typeof value[key] !== 'string' || Number.isNaN(Date.parse(value[key]))) {
      throw new Error('Invalid project timestamps.');
    }
  }
  if (
    !value.sourceVideo ||
    typeof value.sourceVideo.path !== 'string' ||
    typeof value.sourceVideo.sizeBytes !== 'number'
  ) {
    throw new Error('Invalid project source video.');
  }
  if (!PROCESSING_STATUSES.has(value.processingStatus)) {
    throw new Error('Invalid processing status.');
  }
  return value;
}

function writeAtomic(filePath, text) {
  const tmpPath = `${filePath}.tmp-${process.pid}`;
  fs.writeFileSync(tmpPath, text);
  fs.renameSync(tmpPath, filePath);
}

function readMetadata(projectDir) {
  const raw = fs.readFileSync(path.join(projectDir, 'project.json'), 'utf8');
  return validateMetadata(JSON.parse(raw));
}

function toPublicMetadata(meta) {
  return {
    id: meta.id,
    name: meta.name,
    createdAt: meta.createdAt,
    updatedAt: meta.updatedAt,
    lastOpenedAt: meta.lastOpenedAt,
    sourceVideo: {
      path: meta.sourceVideo.path,
      sizeBytes: meta.sourceVideo.sizeBytes,
    },
    processingStatus: meta.processingStatus,
    schemaVersion: meta.schemaVersion,
  };
}

/** List valid projects, most recently opened first. Invalid entries are skipped. */
export function listProjects(userDataPath) {
  const root = getProjectsRoot(userDataPath);
  let entries = [];
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch (err) {
    if (err?.code === 'ENOENT') {
      return [];
    }
    throw err;
  }
  const projects = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith('.tmp-')) {
      continue;
    }
    try {
      projects.push(toPublicMetadata(readMetadata(path.join(root, entry.name))));
    } catch {
      continue;
    }
  }
  projects.sort((a, b) => {
    const key = (p) => p.lastOpenedAt || p.updatedAt || p.createdAt || '';
    return key(b).localeCompare(key(a));
  });
  return projects;
}

/** Load one project by id. Throws on unknown, invalid, or corrupt entries. */
export function getProject(userDataPath, id) {
  const root = getProjectsRoot(userDataPath);
  return toPublicMetadata(readMetadata(resolveProjectDir(root, id)));
}

/**
 * Create a project from a validated local video file. Copies the source
 * into an isolated project directory using a stage-then-rename flow, so
 * a failed import never leaves a partial project behind.
 */
export function createProject(userDataPath, { name, sourcePath } = {}) {
  const cleanName = validateName(name);
  if (typeof sourcePath !== 'string' || sourcePath === '') {
    throw new Error('A source video path is required.');
  }
  const inputError = validateVideoInput(sourcePath);
  if (inputError) {
    throw new Error(inputError);
  }
  let stat = null;
  try {
    stat = fs.statSync(sourcePath);
  } catch {
    stat = null;
  }
  if (!stat || !stat.isFile()) {
    throw new Error('The selected file could not be read.');
  }

  const root = getProjectsRoot(userDataPath);
  fs.mkdirSync(root, { recursive: true });

  const slug = slugify(cleanName);
  let id = null;
  let finalDir = null;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const candidate = `${slug}-${crypto.randomBytes(3).toString('hex')}`;
    const candidateDir = path.join(root, candidate);
    if (!fs.existsSync(candidateDir)) {
      id = candidate;
      finalDir = candidateDir;
      break;
    }
  }
  if (!id) {
    throw new Error('Could not allocate a project directory.');
  }

  const stagingDir = path.join(root, `.tmp-${id}`);
  fs.rmSync(stagingDir, { recursive: true, force: true });
  try {
    const ext = path.extname(sourcePath).toLowerCase() || '.mp4';
    const sourceDir = path.join(stagingDir, 'source');
    const outputDir = path.join(stagingDir, 'output');
    const audioDir = path.join(stagingDir, 'audio', 'commentary');
    fs.mkdirSync(sourceDir, { recursive: true });
    fs.mkdirSync(outputDir, { recursive: true });
    fs.mkdirSync(audioDir, { recursive: true });
    const destVideo = path.join(sourceDir, `gameplay${ext}`);
    fs.copyFileSync(sourcePath, destVideo);
    const copied = fs.statSync(destVideo);
    const now = new Date().toISOString();
    const finalVideo = path.join(finalDir, 'source', `gameplay${ext}`);
    const metadata = {
      schemaVersion: SCHEMA_VERSION,
      id,
      name: cleanName,
      createdAt: now,
      updatedAt: now,
      lastOpenedAt: now,
      sourceVideo: { path: finalVideo, sizeBytes: copied.size },
      processingStatus: 'idle',
    };
    writeAtomic(path.join(stagingDir, 'project.json'), JSON.stringify(metadata, null, 2));
    fs.renameSync(stagingDir, finalDir);
    return toPublicMetadata(validateMetadata(metadata));
  } catch (err) {
    fs.rmSync(stagingDir, { recursive: true, force: true });
    throw err;
  }
}

/** Update allow-listed mutable fields (processingStatus, lastOpenedAt). */
export function updateProject(userDataPath, id, patch = {}) {
  const root = getProjectsRoot(userDataPath);
  const dir = resolveProjectDir(root, id);
  const meta = readMetadata(dir);
  if (!patch || typeof patch !== 'object') {
    throw new Error('Invalid project update.');
  }
  const next = { ...meta, sourceVideo: { ...meta.sourceVideo } };
  for (const [key, value] of Object.entries(patch)) {
    if (!PATCHABLE_FIELDS.has(key)) {
      throw new Error(`Project field '${key}' cannot be updated.`);
    }
    if (key === 'processingStatus' && !PROCESSING_STATUSES.has(value)) {
      throw new Error('Invalid processing status.');
    }
    if (key === 'lastOpenedAt' && (typeof value !== 'string' || Number.isNaN(Date.parse(value)))) {
      throw new Error('Invalid timestamp.');
    }
    next[key] = value;
  }
  next.updatedAt = new Date().toISOString();
  writeAtomic(path.join(dir, 'project.json'), JSON.stringify(validateMetadata(next), null, 2));
  return toPublicMetadata(next);
}

/**
 * Backstop writer for project run status. Transitions a project out of
 * 'processing' only: normal runs are recorded by the renderer first, so
 * a write here means the recorder is gone and the status would
 * otherwise lie. Never marks incomplete work successful. Returns the
 * current public metadata and whether a transition happened.
 */
export function settleProjectRun(userDataPath, id, status) {
  if (!PROCESSING_STATUSES.has(status) || status === 'processing' || status === 'idle') {
    throw new Error('Invalid settle status.');
  }
  const root = getProjectsRoot(userDataPath);
  const meta = readMetadata(resolveProjectDir(root, id));
  if (meta.processingStatus !== 'processing') {
    return { settled: false, project: toPublicMetadata(meta) };
  }
  return {
    settled: true,
    project: updateProject(userDataPath, id, { processingStatus: status }),
  };
}

/**
 * Startup recovery: list every project still recorded as 'processing'
 * and move it to 'interrupted'. Call only when no worker can be alive
 * (fresh process); never touches genuinely active runs.
 */
export function reconcileInterruptedRuns(userDataPath) {
  const reconciled = [];
  for (const project of listProjects(userDataPath)) {
    if (project.processingStatus === 'processing') {
      settleProjectRun(userDataPath, project.id, 'interrupted');
      reconciled.push(project.id);
    }
  }
  return reconciled;
}

/** Mark a project opened (refreshes lastOpenedAt) and return it. */
export function openProject(userDataPath, id) {
  return updateProject(userDataPath, id, {
    lastOpenedAt: new Date().toISOString(),
  });
}

/** Delete a project and its isolated directory tree. */
export function deleteProject(userDataPath, id) {
  const root = getProjectsRoot(userDataPath);
  const dir = resolveProjectDir(root, id);
  if (!fs.existsSync(path.join(dir, 'project.json'))) {
    throw new Error('Project not found.');
  }
  fs.rmSync(dir, { recursive: true, force: true });
  return { deleted: true, id };
}

/**
 * Artifact discovery and reads. All paths resolve main-side from a
 * validated project id; the renderer only ever sees names the main
 * process listed. JSON documents are parsed and size-capped here so
 * corrupt or hostile files fail fast instead of reaching the UI.
 */

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

const MEDIA_SCOPES = {
  frames: { dir: ['output', 'frames'], extensions: new Set(['.jpg', '.jpeg', '.png']) },
  audio: { dir: ['audio'], extensions: new Set(['.mp3', '.wav', '.ogg', '.m4a']) },
  'audio/commentary': {
    dir: ['audio', 'commentary'],
    extensions: new Set(['.mp3', '.wav', '.ogg', '.m4a']),
  },
};

/**
 * Resolve one media file for the `project://` protocol handler: returns
 * the absolute path after validating the id, scope, filename, and
 * extension, and confirming the file exists inside the project. Any
 * deviation throws; the caller maps failures to 404 without detail.
 */
export function resolveProjectMedia(userDataPath, projectId, scope, name) {
  const media = Object.prototype.hasOwnProperty.call(MEDIA_SCOPES, scope)
    ? MEDIA_SCOPES[scope]
    : null;
  if (!media) {
    throw new Error('Unsupported media scope.');
  }
  if (typeof name !== 'string' || !/^[A-Za-z0-9_.-]{1,64}$/.test(name)) {
    throw new Error('Invalid media name.');
  }
  if (name.startsWith('.') || !media.extensions.has(path.extname(name).toLowerCase())) {
    throw new Error('Unsupported media type.');
  }
  const root = getProjectsRoot(userDataPath);
  const dir = resolveProjectDir(root, projectId);
  if (!fs.existsSync(path.join(dir, 'project.json'))) {
    throw new Error('Project not found.');
  }
  const filePath = projectSubdir(dir, ...media.dir, name);
  let stat = null;
  try {
    stat = fs.statSync(filePath);
  } catch {
    stat = null;
  }
  if (!stat || !stat.isFile()) {
    throw new Error('Media not found.');
  }
  return filePath;
}

/** Output directory for a project's isolated worker artifacts. */
export function getProjectOutputDir(userDataPath, id) {
  const root = getProjectsRoot(userDataPath);
  const dir = resolveProjectDir(root, id);
  if (!fs.existsSync(path.join(dir, 'project.json'))) {
    throw new Error('Project not found.');
  }
  return path.join(dir, 'output');
}
