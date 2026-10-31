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

/** Output directory for a project's isolated worker artifacts. */
export function getProjectOutputDir(userDataPath, id) {
  const root = getProjectsRoot(userDataPath);
  const dir = resolveProjectDir(root, id);
  if (!fs.existsSync(path.join(dir, 'project.json'))) {
    throw new Error('Project not found.');
  }
  return path.join(dir, 'output');
}
