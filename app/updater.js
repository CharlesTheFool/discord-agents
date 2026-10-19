// Framework self-update. The Electron shell ships only the launcher; the
// framework (Python + the web UI the daemon serves) is a git checkout. On a
// new release `main` advances, so a production install catches up by
// fast-forwarding to origin/main before the daemon starts on the new code.
//
// Deliberately dependency-free and Electron-free so it can be unit-tested with
// plain node against a throwaway git repo (see test/updater.test.js).

const { execFileSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

function git(root, args) {
  return execFileSync("git", ["-C", root, ...args], {
    encoding: "utf-8",
    windowsHide: true,
  }).trim();
}

// Bring a git-checkout framework install up to the latest release.
//
// Gates (any failed gate => no-op, never throws into the caller):
//   - root is a git checkout
//   - HEAD is on `branch` (default "main"); dev branches are left alone
//   - tracked tree is clean (untracked files are fine); a skip names the
//     modified files, so the dashboard can say exactly what's in the way
//   - origin/branch is strictly ahead and fast-forwardable
// Dependencies are ensureDeps' job, not this one's.
// Returns { status, ... }: "updated" | "current" | "skipped" | "error".
function updateFrameworkRepo({ root, branch = "main", log = () => {} }) {
  try {
    if (!fs.existsSync(path.join(root, ".git"))) {
      return { status: "skipped", reason: "not a git checkout" };
    }

    const current = git(root, ["rev-parse", "--abbrev-ref", "HEAD"]);
    if (current !== branch) {
      return { status: "skipped", reason: `on '${current}', not '${branch}'` };
    }

    const dirty = git(root, ["status", "--porcelain", "--untracked-files=no"]);
    if (dirty) {
      // porcelain lines are "XY path"; git() trimmed the first one's leading space
      const files = dirty.split("\n").map((line) => line.trim().replace(/^\S+\s+/, ""));
      return { status: "skipped", reason: "tracked files modified locally", files };
    }

    const before = git(root, ["rev-parse", "HEAD"]);
    log(`fetching origin/${branch}…`);
    git(root, ["fetch", "--quiet", "origin", branch]);

    const behind = parseInt(git(root, ["rev-list", "--count", `HEAD..origin/${branch}`]), 10);
    if (!behind) return { status: "current", head: before };

    log(`fast-forwarding ${behind} commit(s)…`);
    try {
      git(root, ["merge", "--ff-only", `origin/${branch}`]);
    } catch (e) {
      return { status: "skipped", reason: "local history diverged from origin" };
    }
    return { status: "updated", from: before, to: git(root, ["rev-parse", "HEAD"]), behind };
  } catch (e) {
    return { status: "error", reason: e.message };
  }
}

function defaultInstallDeps(python, reqPath, cwd) {
  execFileSync(python, ["-m", "pip", "install", "-r", reqPath], {
    cwd,
    encoding: "utf-8",
    windowsHide: true,
  });
}

// Install requirements.txt whenever it differs from the last successful
// install, recorded as its hash in `stamp`. A failed pip leaves the stamp
// alone, so the next launch tries again - judging by the incoming git range
// instead never retried: once the fast-forward landed, nothing was "new".
// Returns { status }: "current" | "installed" | "error".
function ensureDeps({ root, python = "python", stamp, log = () => {}, installDeps = defaultInstallDeps }) {
  const reqPath = path.join(root, "requirements.txt");
  try {
    const hash = crypto.createHash("sha256").update(fs.readFileSync(reqPath)).digest("hex");
    let last = null;
    try { last = fs.readFileSync(stamp, "utf-8").trim(); } catch (e) { /* never installed here */ }
    if (last === hash) return { status: "current" };
    log("requirements.txt differs from the last install - installing dependencies…");
    installDeps(python, reqPath, root);
    fs.mkdirSync(path.dirname(stamp), { recursive: true });
    fs.writeFileSync(stamp, hash + "\n");
    return { status: "installed" };
  } catch (e) {
    return { status: "error", reason: e.message };
  }
}

module.exports = { updateFrameworkRepo, ensureDeps };
