// Unit tests for the framework self-updater against throwaway git repos.
//   node test/updater.test.js   (or: npm test)

const assert = require("assert");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const { updateFrameworkRepo } = require("../updater");

function git(cwd, ...args) {
  return execFileSync("git", ["-C", cwd, ...args], { encoding: "utf-8", windowsHide: true }).trim();
}

function commit(repo, file, body, msg) {
  fs.writeFileSync(path.join(repo, file), body);
  git(repo, "add", "-A");
  git(repo, "commit", "-q", "-m", msg);
}

// origin (advances on "release") + a clone that lags behind it.
function makeRepos() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "fw-update-"));
  const origin = path.join(tmp, "origin");
  fs.mkdirSync(origin);
  git(origin, "init", "-q", "-b", "main");
  git(origin, "config", "user.email", "t@t.t");
  git(origin, "config", "user.name", "t");
  commit(origin, "requirements.txt", "anthropic\n", "init");
  commit(origin, "core.py", "v1\n", "v1");

  const work = path.join(tmp, "work");
  git(tmp, "clone", "-q", origin, work);
  git(work, "config", "user.email", "t@t.t");
  git(work, "config", "user.name", "t");

  return { tmp, origin, work };
}

function advanceOrigin(origin, { touchReqs = false } = {}) {
  if (touchReqs) commit(origin, "requirements.txt", "anthropic\naiohttp\n", "bump deps");
  else commit(origin, "core.py", "v2\n", "v2");
}

let passed = 0;
function test(name, fn) {
  try { fn(); console.log(`  ok  ${name}`); passed++; }
  catch (e) { console.error(`FAIL  ${name}\n      ${e.message}`); process.exitCode = 1; }
}

// --- up to date -------------------------------------------------------------
test("current when origin has nothing new", () => {
  const { work } = makeRepos();
  const r = updateFrameworkRepo({ root: work });
  assert.strictEqual(r.status, "current", JSON.stringify(r));
});

// --- behind -> fast-forward -------------------------------------------------
test("fast-forwards when origin is ahead", () => {
  const { origin, work } = makeRepos();
  const headBefore = git(work, "rev-parse", "HEAD");
  advanceOrigin(origin);
  const r = updateFrameworkRepo({ root: work });
  assert.strictEqual(r.status, "updated", JSON.stringify(r));
  assert.strictEqual(r.behind, 1, "should be 1 behind");
  assert.notStrictEqual(git(work, "rev-parse", "HEAD"), headBefore, "HEAD must move");
  assert.strictEqual(fs.readFileSync(path.join(work, "core.py"), "utf-8").replace(/\r/g, ""), "v2\n");
  assert.strictEqual(r.pip, false, "no pip when requirements unchanged");
});

// --- pip only when requirements change --------------------------------------
test("runs pip only when requirements.txt is in the range", () => {
  const { origin, work } = makeRepos();
  advanceOrigin(origin, { touchReqs: true });
  const calls = [];
  const r = updateFrameworkRepo({
    root: work,
    python: "py.exe",
    installDeps: (python, reqPath) => calls.push({ python, reqPath }),
  });
  assert.strictEqual(r.status, "updated", JSON.stringify(r));
  assert.strictEqual(r.pip, true, "pip should run");
  assert.strictEqual(calls.length, 1, "installDeps called exactly once");
  assert.strictEqual(calls[0].python, "py.exe");
  assert.match(calls[0].reqPath, /requirements\.txt$/);
});

test("does not run pip when requirements.txt unchanged", () => {
  const { origin, work } = makeRepos();
  advanceOrigin(origin); // touches core.py only
  let called = false;
  const r = updateFrameworkRepo({ root: work, installDeps: () => { called = true; } });
  assert.strictEqual(r.status, "updated", JSON.stringify(r));
  assert.strictEqual(called, false, "installDeps must not be called");
});

// --- gates ------------------------------------------------------------------
test("skips a dev branch", () => {
  const { origin, work } = makeRepos();
  advanceOrigin(origin);
  git(work, "checkout", "-q", "-b", "Beta");
  const r = updateFrameworkRepo({ root: work });
  assert.strictEqual(r.status, "skipped");
  assert.match(r.reason, /Beta/);
});

test("skips when tracked files are dirty", () => {
  const { origin, work } = makeRepos();
  advanceOrigin(origin);
  fs.writeFileSync(path.join(work, "core.py"), "local edit\n");
  const r = updateFrameworkRepo({ root: work });
  assert.strictEqual(r.status, "skipped");
  assert.match(r.reason, /tracked/);
});

test("tolerates untracked files (still updates)", () => {
  const { origin, work } = makeRepos();
  advanceOrigin(origin);
  fs.writeFileSync(path.join(work, "scratch.txt"), "untracked\n");
  const r = updateFrameworkRepo({ root: work });
  assert.strictEqual(r.status, "updated", JSON.stringify(r));
});

test("skips a non-git directory", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "fw-nogit-"));
  const r = updateFrameworkRepo({ root: tmp });
  assert.strictEqual(r.status, "skipped");
  assert.match(r.reason, /not a git checkout/);
});

test("skips (not error) when history diverged", () => {
  const { origin, work } = makeRepos();
  advanceOrigin(origin);
  // Local commit on main that isn't an ancestor of origin/main -> not ff-able.
  commit(work, "core.py", "divergent\n", "local only");
  const r = updateFrameworkRepo({ root: work });
  assert.strictEqual(r.status, "skipped");
  assert.match(r.reason, /diverged/);
});

console.log(`\n${passed} passed`);
