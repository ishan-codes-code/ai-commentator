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
  listArtifacts,
  listProjects,
  openProject,
  readArtifact,
  reconcileInterruptedRuns,
  resolveProjectMedia,
  settleProjectRun,
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

test('interrupted status round-trips for startup recovery', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const created = createProject(base, { name: 'Crash', sourcePath: video });
  // Simulate a run in flight when the session died.
  updateProject(base, created.id, { processingStatus: 'processing' });
  const stale = listProjects(base).find((p) => p.id === created.id);
  assert.equal(stale.processingStatus, 'processing');
  // Recovery marks it interrupted — never successful.
  const recovered = updateProject(base, created.id, {
    processingStatus: 'interrupted',
  });
  assert.equal(recovered.processingStatus, 'interrupted');
  assert.equal(getProject(base, created.id).processingStatus, 'interrupted');
  // A new run can start again from the recovered state.
  const rerun = updateProject(base, created.id, {
    processingStatus: 'processing',
  });
  assert.equal(rerun.processingStatus, 'processing');
});

test('settle only transitions out of processing, never to success', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const running = createProject(base, { name: 'Running', sourcePath: video });
  const done = createProject(base, { name: 'Done', sourcePath: video });
  updateProject(base, running.id, { processingStatus: 'processing' });
  updateProject(base, done.id, { processingStatus: 'done' });

  const settled = settleProjectRun(base, running.id, 'error');
  assert.equal(settled.settled, true);
  assert.equal(settled.project.processingStatus, 'error');

  const untouched = settleProjectRun(base, done.id, 'error');
  assert.equal(untouched.settled, false);
  assert.equal(untouched.project.processingStatus, 'done');

  assert.throws(() => settleProjectRun(base, running.id, 'processing'), /Invalid settle/);
  assert.throws(() => settleProjectRun(base, running.id, 'idle'), /Invalid settle/);
  assert.throws(() => settleProjectRun(base, running.id, 'bogus'), /Invalid settle/);
  assert.throws(() => settleProjectRun(base, '../evil', 'error'), /Invalid project id/);
});

test('reconcile moves only stale processing projects to interrupted', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const stale = createProject(base, { name: 'Stale', sourcePath: video });
  const fresh = createProject(base, { name: 'Fresh', sourcePath: video });
  updateProject(base, stale.id, { processingStatus: 'processing' });

  assert.deepEqual(reconcileInterruptedRuns(base), [stale.id]);
  assert.equal(getProject(base, stale.id).processingStatus, 'interrupted');
  assert.equal(getProject(base, fresh.id).processingStatus, 'idle');
  // Second pass is a no-op: nothing processing remains.
  assert.deepEqual(reconcileInterruptedRuns(base), []);
  // Empty userData root reconciles cleanly.
  const { base: empty, cleanup: cleanupEmpty } = makeUserData();
  t.after(cleanupEmpty);
  assert.deepEqual(reconcileInterruptedRuns(empty), []);
});

test('artifact listing is honest about absent outputs', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const created = createProject(base, { name: 'Empty', sourcePath: video });

  const listing = listArtifacts(base, created.id);
  assert.equal(listing.projectId, created.id);
  assert.deepEqual(listing.frames, { count: 0, items: [] });
  assert.deepEqual(listing.observations, { count: 0, items: [] });
  assert.deepEqual(listing.state, { present: false, invalid: false });
  assert.deepEqual(listing.events, { present: false, invalid: false, count: 0 });
  assert.deepEqual(listing.commentary, { present: false, invalid: false, lines: 0 });
  assert.deepEqual(listing.audio, { files: [] });
  assert.throws(() => listArtifacts(base, 'no-such'), /Project not found/);
  assert.throws(() => listArtifacts(base, '../evil'), /Invalid project id/);
});

test('artifact listing validates entries and reads documents', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const created = createProject(base, { name: 'Seeded', sourcePath: video });
  const out = getProjectOutputDir(base, created.id);

  fs.mkdirSync(path.join(out, 'frames'), { recursive: true });
  fs.writeFileSync(path.join(out, 'frames', 'frame_00003.jpg'), Buffer.alloc(16));
  fs.writeFileSync(path.join(out, 'frames', 'frame_00000.jpg'), Buffer.alloc(16));
  fs.writeFileSync(path.join(out, 'frames', 'notes.txt'), 'ignore me');
  const obsDir = path.join(out, 'observations');
  fs.mkdirSync(obsDir, { recursive: true });
  const obs = {
    schema_version: 'observation-v1',
    timestamp: 3,
    observations: [
      { category: 'environment', description: 'Dusty pitch.', confidence: 'high', evidence: 'seed' },
    ],
    uncertainties: [],
  };
  fs.writeFileSync(path.join(obsDir, 'frame_00003.json'), JSON.stringify(obs));
  fs.writeFileSync(path.join(obsDir, 'frame_00006.json'), '{broken');
  const eventsDir = path.join(out, 'events');
  fs.mkdirSync(eventsDir, { recursive: true });
  fs.writeFileSync(
    path.join(eventsDir, 'events.json'),
    JSON.stringify({ schema_version: 'events-v1', events: [{ type: 'x', timestamp: 3 }] }),
  );
  const audioDir = path.join(getProjectsRoot(base), created.id, 'audio');
  fs.writeFileSync(path.join(audioDir, 'take.mp3'), Buffer.alloc(8));
  fs.writeFileSync(path.join(audioDir, 'readme.txt'), 'not audio');

  const listing = listArtifacts(base, created.id);
  assert.equal(listing.frames.count, 2);
  assert.deepEqual(
    listing.frames.items.map((item) => item.name),
    ['frame_00000.jpg', 'frame_00003.jpg'],
  );
  assert.equal(listing.frames.items[1].timestamp, 3);
  assert.equal(listing.observations.count, 2);
  assert.deepEqual(listing.state, { present: false, invalid: false });
  assert.equal(listing.events.count, 1);
  assert.deepEqual(listing.audio.files.map((f) => f.name), ['take.mp3']);
  assert.equal(listing.audio.files[0].scope, 'audio');

  const doc = readArtifact(base, created.id, 'observation', 'frame_00003.json');
  assert.equal(doc.kind, 'observation');
  assert.equal(doc.data.timestamp, 3);
  assert.equal(doc.data.observations[0].category, 'environment');
  assert.throws(
    () => readArtifact(base, created.id, 'observation', 'frame_00006.json'),
    /not valid JSON/,
  );
  assert.throws(
    () => readArtifact(base, created.id, 'observation', '../../evil.json'),
    /Invalid artifact name/,
  );
  assert.throws(
    () => readArtifact(base, created.id, 'observation', 'match_state.json'),
    /Invalid artifact name/,
  );
  assert.throws(
    () => readArtifact(base, created.id, 'frames', 'frame_00003.jpg'),
    /Unsupported artifact kind/,
  );
  assert.throws(
    () => readArtifact(base, created.id, 'state', 'wrong.json'),
    /Invalid artifact name/,
  );
  assert.throws(
    () => readArtifact(base, created.id, 'commentary', 'commentary.json'),
    /not found/i,
  );
  const eventsDoc = readArtifact(base, created.id, 'events', 'events.json');
  assert.equal(eventsDoc.data.events.length, 1);
});

test('media resolver confines access to listed project files', (t) => {
  const { base, cleanup } = makeUserData();
  t.after(cleanup);
  const video = makeSourceVideo(path.join(base, 'incoming'));
  const created = createProject(base, { name: 'Media', sourcePath: video });
  const out = getProjectOutputDir(base, created.id);
  fs.mkdirSync(path.join(out, 'frames'), { recursive: true });
  fs.writeFileSync(path.join(out, 'frames', 'frame_00003.jpg'), Buffer.alloc(16));
  fs.writeFileSync(path.join(out, 'frames', 'notes.txt'), 'nope');

  const resolved = resolveProjectMedia(base, created.id, 'frames', 'frame_00003.jpg');
  assert.equal(resolved, path.join(out, 'frames', 'frame_00003.jpg'));
  assert.throws(() => resolveProjectMedia(base, created.id, 'frames', 'notes.txt'), /Unsupported media type/);
  assert.throws(() => resolveProjectMedia(base, created.id, 'output', 'frames'), /Unsupported media scope/);
  assert.throws(() => resolveProjectMedia(base, created.id, 'frames', '../evil.jpg'), /Invalid media name/);
  assert.throws(() => resolveProjectMedia(base, created.id, 'frames', '.hidden.jpg'), /Invalid media name|Unsupported media type/);
  assert.throws(() => resolveProjectMedia(base, created.id, 'frames', 'missing.jpg'), /Media not found/);
  assert.throws(() => resolveProjectMedia(base, '../evil', 'frames', 'frame_00003.jpg'), /Invalid project id/);
  assert.throws(() => resolveProjectMedia(base, 'no-such', 'frames', 'frame_00003.jpg'), /Project not found/);
});
