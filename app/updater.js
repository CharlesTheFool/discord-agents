// Framework self-update. The Electron shell ships only the launcher; the
// framework (Python + the web UI the daemon serves) is a git checkout. On a
// new release `main` advances, so a production install catches up by
// fast-forwarding to origin/main before the daemon starts on the new code.
//
// Deliberately dependency-free and Electron-free so it can be unit-tested with
// plain node against a throwaway git repo (see test/updater.test.js).

const { execFileSync } = require("child_process");
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
//   - tracked tree is clean (untracked files are fine)
//   - origin/branch is strictly ahead and fast-forwardable
// pip runs only when requirements.txt changed across the incoming range.
//
// `installDeps` is the seam for that pip call (injectable for tests).
// Returns { status, ... }: "updated" | "current" | "skipped" | "error".
function defaultInstallDeps(python, reqPath, cwd) {
  execFileSync(python, ["-m", "pip", "install", "-r", reqPath], {
    cwd,
    encoding: "utf-8",
    windowsHide: true,
  });
}

function updateFrameworkRepo({
  root,
  python = "python",
  branch = "main",
  log = () => {},
  installDeps = defaultInstallDeps,
}) {
  try {
    if (!fs.existsSync(path.join(root, ".git"))) {
      return { status: "skipped", reason: "not a git checkout" };
    }

    const current = git(root, ["rev-parse", "--abbrev-ref", "HEAD"]);
    if (current !== branch) {
      return { status: "skipped", reason: `on '${current}', not '${branch}'` };
    }

    if (git(root, ["status", "--porcelain", "--untracked-files=no"])) {
      return { status: "skipped", reason: "tracked files modified locally" };
    }

    const before = git(root, ["rev-parse", "HEAD"]);
    log(`fetching origin/${branch}…`);
    git(root, ["fetch", "--quiet", "origin", branch]);

    const behind = parseInt(git(root, ["rev-list", "--count", `HEAD..origin/${branch}`]), 10);
    if (!behind) return { status: "current", head: before };

    // Inspect the incoming range before moving HEAD so we know whether deps shifted.
    const changed = git(root, ["diff", "--name-only", `HEAD..origin/${branch}`]).split("\n");
    const reqChanged = changed.includes("requirements.txt");

    log(`fast-forwarding ${behind} commit(s)…`);
    try {
      git(root, ["merge", "--ff-only", `origin/${branch}`]);
    } catch (e) {
      return { status: "skipped", reason: "local history diverged from origin" };
    }
    const after = git(root, ["rev-parse", "HEAD"]);

    let pip = false;
    if (reqChanged) {
      log("requirements.txt changed — installing dependencies…");
      installDeps(python, path.join(root, "requirements.txt"), root);
      pip = true;
    }

    return { status: "updated", from: before, to: after, behind, pip };
  } catch (e) {
    return { status: "error", reason: e.message };
  }
}

module.exports = { updateFrameworkRepo };
