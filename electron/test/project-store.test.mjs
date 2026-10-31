import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  SCHEMA_VERSION,
  createProject,
  deleteProject,
  getProject,
  getProjectOutputDir,
  getProjectsRoot,
  listProjects,
  openProject,
  updateProject,
} from '../src/project-store.mjs';

let rootCounter = 0;

function makeUserData() {
  rootCounter += 1;
  const base = fs.mkdtempSync(
    path.join(os.tmpdir(), `ai-commentator-projects-${process.pid}-${rootCounter}-`),
  );
  return { base, cleanup: () => fs.rmSync(base, { recursive: true, force: true }) };
}

function makeSourceVideo(dir, name = 'match.mp4', bytes = 1024) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, Buffer.alloc(bytes, 7));
  return file;
}

test('create + get roundtrip isolates each project directory', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const srcDir = path.join(base, 'incoming');
  const video = makeSourceVideo(srcDir);

  const created = createProject(base, { name: 'Friday Finals', sourcePath: video });
  assert.match(created.id, /^[a-z0-9-]{1,64}$/);
  assert.equal(created.name, 'Friday Finals');
  assert.equal(created.schemaVersion, SCHEMA_VERSION);
  assert.equal(created.processingStatus, 'idle');
  assert.ok(created.sourceVideo.path.endsWith(path.join('source', 'gameplay.mp4')));
  assert.equal(created.sourceVideo.sizeBytes, 1024);

  const dir = path.join(getProjectsRoot(base), created.id);
  assert.ok(fs.statSync(path.join(dir, 'project.json')).isFile());
  assert.ok(fs.statSync(created.sourceVideo.path).isFile());
  assert.ok(fs.statSync(path.join(dir, 'output')).isDirectory());
  assert.ok(fs.statSync(path.join(dir, 'audio', 'commentary')).isDirectory());
  // Source fixture untouched; project holds its own copy.
  assert.ok(fs.statSync(video).isFile());

  const loaded = getProject(base, created.id);
  assert.deepEqual(loaded, created);

  const second = createProject(base, { name: 'Friday Finals', sourcePath: video });
  assert.notEqual(second.id, created.id);
  assert.notEqual(
    getProjectOutputDir(base, second.id),
    getProjectOutputDir(base, created.id),
  );
});

test('create rejects invalid input without staging residue', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const srcDir = path.join(base, 'incoming');
  const video = makeSourceVideo(srcDir);
  const notes = path.join(srcDir, 'notes.txt');
  fs.writeFileSync(notes, 'x');

  assert.throws(() => createProject(base, { name: '  ', sourcePath: video }), /empty/);
  assert.throws(() => createProject(base, { name: 'x'.repeat(101), sourcePath: video }), /too long/);
  assert.throws(() => createProject(base, { name: 'ok', sourcePath: notes }), /extension|supported/i);
  assert.throws(
    () => createProject(base, { name: 'ok', sourcePath: path.join(srcDir, 'missing.mp4') }),
    /could not be read/i,
  );
  assert.throws(
    () => createProject(base, { name: 'ok', sourcePath: srcDir }),
    /extension|supported|could not be read/i,
  );

  const root = getProjectsRoot(base);
  const leftovers = fs.existsSync(root)
    ? fs.readdirSync(root).filter((name) => name.startsWith('.tmp-'))
    : [];
  assert.deepEqual(leftovers, []);
  assert.deepEqual(listProjects(base), []);
});

test('ids are allow-listed and contained in the projects root', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const created = createProject(base, { name: 'Contained', sourcePath: video });

  for (const bad of ['../evil', 'a/b', '', '.', '..', 'x'.repeat(65), null]) {
    assert.throws(() => getProject(base, bad), /Invalid project id/);
    assert.throws(() => updateProject(base, bad, { processingStatus: 'done' }), /Invalid project id/);
    assert.throws(() => deleteProject(base, bad), /Invalid project id/);
    assert.throws(() => getProjectOutputDir(base, bad), /Invalid project id/);
  }
  assert.throws(() => getProject(base, 'no-such-project'), /ENOENT|not found/i);
  assert.throws(() => deleteProject(base, 'no-such-project'), /not found/i);
  // Real project still intact.
  assert.equal(getProject(base, created.id).name, 'Contained');
});

test('updates only allow-listed fields and refresh on open', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const created = createProject(base, { name: 'Status', sourcePath: video });

  const done = updateProject(base, created.id, { processingStatus: 'done' });
  assert.equal(done.processingStatus, 'done');
  assert.throws(
    () => updateProject(base, created.id, { name: 'Renamed' }),
    /cannot be updated/,
  );
  assert.throws(
    () => updateProject(base, created.id, { processingStatus: 'bogus' }),
    /Invalid processing status/,
  );
  assert.throws(
    () => updateProject(base, created.id, { sourceVideo: { path: '/x', sizeBytes: 1 } }),
    /cannot be updated/,
  );

  const before = getProject(base, created.id).lastOpenedAt;
  const opened = openProject(base, created.id);
  assert.ok(opened.lastOpenedAt >= before);
});

test('list orders by recency and skips corrupt entries', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const first = createProject(base, { name: 'First', sourcePath: video });
  const second = createProject(base, { name: 'Second', sourcePath: video });
  openProject(base, first.id);

  const root = getProjectsRoot(base);
  fs.mkdirSync(path.join(root, 'broken'));
  fs.writeFileSync(path.join(root, 'broken', 'project.json'), '{not json');
  fs.mkdirSync(path.join(root, 'not-a-project'));

  const listed = listProjects(base).map((p) => p.id);
  assert.deepEqual(listed, [first.id, second.id]);
  assert.ok(!listed.includes('broken'));
  assert.ok(!listed.includes('not-a-project'));
});

test('delete removes exactly one project tree', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const keep = createProject(base, { name: 'Keep', sourcePath: video });
  const drop = createProject(base, { name: 'Drop', sourcePath: video });

  assert.deepEqual(deleteProject(base, drop.id), { deleted: true, id: drop.id });
  assert.ok(!fs.existsSync(path.join(getProjectsRoot(base), drop.id)));
  assert.equal(getProject(base, keep.id).name, 'Keep');
  assert.deepEqual(
    listProjects(base).map((p) => p.id),
    [keep.id],
  );
});
