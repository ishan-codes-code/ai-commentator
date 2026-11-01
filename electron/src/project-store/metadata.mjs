import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { validateVideoInput } from '../worker-protocol.mjs';

export const SCHEMA_VERSION = 1;
export const PROJECTS_DIR_NAME = 'projects';

const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const MAX_NAME_LENGTH = 100;
const PATCHABLE_FIELDS = new Set(['processingStatus', 'lastOpenedAt']);
const PROCESSING_STATUSES = new Set(['idle', 'processing', 'done', 'stopped', 'error', 'cancelled', 'interrupted']);

export function getProjectsRoot(userDataPath) {
  return path.join(userDataPath, PROJECTS_DIR_NAME);
}

function assertId(id) {
  if (typeof id !== 'string' || !ID_PATTERN.test(id)) throw new Error('Invalid project id.');
}

export function resolveProjectDir(projectsRoot, id) {
  assertId(id);
  const dir = path.join(projectsRoot, id);
  const relative = path.relative(projectsRoot, dir);
  if (relative === '' || relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Invalid project id.');
  return dir;
}

function slugify(name) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  return slug || 'project';
}

function validateName(name) {
  if (typeof name !== 'string') throw new Error('Project name must be a string.');
  const trimmed = name.trim();
  if (trimmed === '') throw new Error('Project name must not be empty.');
  if (trimmed.length > MAX_NAME_LENGTH) throw new Error('Project name is too long.');
  return trimmed;
}

function validateMetadata(value) {
  if (!value || typeof value !== 'object') throw new Error('Invalid project metadata.');
  assertId(value.id);
  validateName(value.name);
  if (value.schemaVersion !== SCHEMA_VERSION) throw new Error('Unsupported project version.');
  for (const key of ['createdAt', 'updatedAt', 'lastOpenedAt']) {
    if (typeof value[key] !== 'string' || Number.isNaN(Date.parse(value[key]))) throw new Error('Invalid project timestamps.');
  }
  if (!value.sourceVideo || typeof value.sourceVideo.path !== 'string' || typeof value.sourceVideo.sizeBytes !== 'number') throw new Error('Invalid project source video.');
  if (!PROCESSING_STATUSES.has(value.processingStatus)) throw new Error('Invalid processing status.');
  return value;
}

function writeAtomic(filePath, text) {
  const tmpPath = `${filePath}.tmp-${process.pid}`;
  fs.writeFileSync(tmpPath, text);
  fs.renameSync(tmpPath, filePath);
}

function readMetadata(projectDir) {
  return validateMetadata(JSON.parse(fs.readFileSync(path.join(projectDir, 'project.json'), 'utf8')));
}

function toPublicMetadata(meta) {
  return {
    id: meta.id, name: meta.name, createdAt: meta.createdAt, updatedAt: meta.updatedAt,
    lastOpenedAt: meta.lastOpenedAt,
    sourceVideo: { path: meta.sourceVideo.path, sizeBytes: meta.sourceVideo.sizeBytes },
    processingStatus: meta.processingStatus, schemaVersion: meta.schemaVersion,
  };
}

export function listProjects(userDataPath) {
  const root = getProjectsRoot(userDataPath);
  let entries = [];
  try { entries = fs.readdirSync(root, { withFileTypes: true }); }
  catch (err) { if (err?.code === 'ENOENT') return []; throw err; }
  const projects = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith('.tmp-')) continue;
    try { projects.push(toPublicMetadata(readMetadata(path.join(root, entry.name)))); } catch { continue; }
  }
  projects.sort((a, b) => {
    const key = (p) => p.lastOpenedAt || p.updatedAt || p.createdAt || '';
    return key(b).localeCompare(key(a));
  });
  return projects;
}

export function getProject(userDataPath, id) {
  return toPublicMetadata(readMetadata(resolveProjectDir(getProjectsRoot(userDataPath), id)));
}

export function createProject(userDataPath, { name, sourcePath } = {}) {
  const cleanName = validateName(name);
  if (typeof sourcePath !== 'string' || sourcePath === '') throw new Error('A source video path is required.');
  const inputError = validateVideoInput(sourcePath);
  if (inputError) throw new Error(inputError);
  let stat = null;
  try { stat = fs.statSync(sourcePath); } catch { stat = null; }
  if (!stat || !stat.isFile()) throw new Error('The selected file could not be read.');
  const root = getProjectsRoot(userDataPath);
  fs.mkdirSync(root, { recursive: true });
  const slug = slugify(cleanName);
  let id = null;
  let finalDir = null;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const candidate = `${slug}-${crypto.randomBytes(3).toString('hex')}`;
    const candidateDir = path.join(root, candidate);
    if (!fs.existsSync(candidateDir)) { id = candidate; finalDir = candidateDir; break; }
  }
  if (!id) throw new Error('Could not allocate a project directory.');
  const stagingDir = path.join(root, `.tmp-${id}`);
  fs.rmSync(stagingDir, { recursive: true, force: true });
  try {
    const ext = path.extname(sourcePath).toLowerCase() || '.mp4';
    const sourceDir = path.join(stagingDir, 'source');
    fs.mkdirSync(sourceDir, { recursive: true });
    fs.mkdirSync(path.join(stagingDir, 'output'), { recursive: true });
    fs.mkdirSync(path.join(stagingDir, 'audio', 'commentary'), { recursive: true });
    const destVideo = path.join(sourceDir, `gameplay${ext}`);
    fs.copyFileSync(sourcePath, destVideo);
    const copied = fs.statSync(destVideo);
    const now = new Date().toISOString();
    const metadata = {
      schemaVersion: SCHEMA_VERSION, id, name: cleanName, createdAt: now, updatedAt: now, lastOpenedAt: now,
      sourceVideo: { path: path.join(finalDir, 'source', `gameplay${ext}`), sizeBytes: copied.size }, processingStatus: 'idle',
    };
    writeAtomic(path.join(stagingDir, 'project.json'), JSON.stringify(metadata, null, 2));
    fs.renameSync(stagingDir, finalDir);
    return toPublicMetadata(validateMetadata(metadata));
  } catch (err) { fs.rmSync(stagingDir, { recursive: true, force: true }); throw err; }
}

export function updateProject(userDataPath, id, patch = {}) {
  const dir = resolveProjectDir(getProjectsRoot(userDataPath), id);
  const meta = readMetadata(dir);
  if (!patch || typeof patch !== 'object') throw new Error('Invalid project update.');
  const next = { ...meta, sourceVideo: { ...meta.sourceVideo } };
  for (const [key, value] of Object.entries(patch)) {
    if (!PATCHABLE_FIELDS.has(key)) throw new Error(`Project field '${key}' cannot be updated.`);
    if (key === 'processingStatus' && !PROCESSING_STATUSES.has(value)) throw new Error('Invalid processing status.');
    if (key === 'lastOpenedAt' && (typeof value !== 'string' || Number.isNaN(Date.parse(value)))) throw new Error('Invalid timestamp.');
    next[key] = value;
  }
  next.updatedAt = new Date().toISOString();
  writeAtomic(path.join(dir, 'project.json'), JSON.stringify(validateMetadata(next), null, 2));
  return toPublicMetadata(next);
}

export function settleProjectRun(userDataPath, id, status) {
  if (!PROCESSING_STATUSES.has(status) || status === 'processing' || status === 'idle') throw new Error('Invalid settle status.');
  const meta = readMetadata(resolveProjectDir(getProjectsRoot(userDataPath), id));
  if (meta.processingStatus !== 'processing') return { settled: false, project: toPublicMetadata(meta) };
  return { settled: true, project: updateProject(userDataPath, id, { processingStatus: status }) };
}

export function reconcileInterruptedRuns(userDataPath) {
  const reconciled = [];
  for (const project of listProjects(userDataPath)) {
    if (project.processingStatus === 'processing') { settleProjectRun(userDataPath, project.id, 'interrupted'); reconciled.push(project.id); }
  }
  return reconciled;
}

export function openProject(userDataPath, id) { return updateProject(userDataPath, id, { lastOpenedAt: new Date().toISOString() }); }

export function deleteProject(userDataPath, id) {
  const dir = resolveProjectDir(getProjectsRoot(userDataPath), id);
  if (!fs.existsSync(path.join(dir, 'project.json'))) throw new Error('Project not found.');
  fs.rmSync(dir, { recursive: true, force: true });
  return { deleted: true, id };
}

export function getProjectOutputDir(userDataPath, id) {
  const dir = resolveProjectDir(getProjectsRoot(userDataPath), id);
  if (!fs.existsSync(path.join(dir, 'project.json'))) throw new Error('Project not found.');
  return path.join(dir, 'output');
}
