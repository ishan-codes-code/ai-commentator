import fs from 'node:fs';
import path from 'node:path';
import { getProjectsRoot, resolveProjectDir } from './metadata.mjs';

function projectSubdir(projectDir, ...parts) {
  const resolved = path.join(projectDir, ...parts);
  const relative = path.relative(projectDir, resolved);
  if (relative === '' || relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Invalid artifact path.');
  return resolved;
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

