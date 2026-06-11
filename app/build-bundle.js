// Stage the self-contained bundle electron-builder ships as extraResources:
//
//   app/bundle/runtime/python/   embeddable CPython + vendored site-packages
//   app/bundle/framework/        the Python framework source (no git, no data)
//
// Run before `npm run dist` / `npm run release`:  node build-bundle.js
//
// The embedded interpreter's minor version MUST match the python used for
// the pip --target install (compiled wheels are tagged per minor version),
// so this script refuses to run under anything but PY_MINOR.

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const https = require("https");

const PY_VERSION = "3.13.5";          // embeddable build to vendor
const PY_MINOR = "3.13";              // must match the local python doing pip
const ROOT = path.join(__dirname, ".."); // the framework repo
const BUNDLE = path.join(__dirname, "bundle");
const RUNTIME = path.join(BUNDLE, "runtime", "python");
const FRAMEWORK = path.join(BUNDLE, "framework");

const FRAMEWORK_ITEMS = [
  "core", "supervisor", "skills",
  "bot_manager.py", "supervisor.py", "deployment_tool.py",
  "requirements.txt", "README.md", "CHANGELOG.md",
];
const EXCLUDE_DIRS = new Set(["__pycache__", ".git", "tests"]);

function log(msg) { process.stdout.write(msg + "\n"); }

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const get = (u) => https.get(u, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location)
        return get(res.headers.location);
      if (res.statusCode !== 200)
        return reject(new Error(`download failed: ${res.statusCode} ${u}`));
      res.pipe(file);
      file.on("finish", () => file.close(resolve));
    }).on("error", reject);
    get(url);
  });
}

function copyFiltered(src, dest) {
  fs.cpSync(src, dest, {
    recursive: true,
    filter: (p) => !EXCLUDE_DIRS.has(path.basename(p)),
  });
}

(async function main() {
  // 0. sanity: local python matches the embedded minor
  const pyv = execFileSync("python", ["--version"], { encoding: "utf-8" }).trim();
  if (!pyv.includes(` ${PY_MINOR}.`)) {
    throw new Error(`local ${pyv} != embedded ${PY_MINOR}.x - compiled wheels would mismatch`);
  }

  fs.rmSync(BUNDLE, { recursive: true, force: true });
  fs.mkdirSync(RUNTIME, { recursive: true });

  // 1. embeddable CPython
  const zipName = `python-${PY_VERSION}-embed-amd64.zip`;
  const zipPath = path.join(BUNDLE, zipName);
  log(`fetching ${zipName}…`);
  await download(`https://www.python.org/ftp/python/${PY_VERSION}/${zipName}`, zipPath);
  execFileSync("powershell", ["-NoProfile", "-Command",
    `Expand-Archive -Path '${zipPath}' -DestinationPath '${RUNTIME}' -Force`]);
  fs.rmSync(zipPath);

  // 2. let the embedded interpreter see vendored site-packages
  const pth = fs.readdirSync(RUNTIME).find((f) => f.endsWith("._pth"));
  fs.appendFileSync(path.join(RUNTIME, pth), "Lib\\site-packages\n");

  // 3. vendor the dependencies (built by the matching local python)
  log("pip install --target (this takes a minute)…");
  execFileSync("python", ["-m", "pip", "install",
    "-r", path.join(ROOT, "requirements.txt"),
    "--target", path.join(RUNTIME, "Lib", "site-packages"),
    "--quiet"], { stdio: "inherit" });

  // 4. the framework source
  log("copying framework…");
  fs.mkdirSync(FRAMEWORK, { recursive: true });
  for (const item of FRAMEWORK_ITEMS) {
    const src = path.join(ROOT, item);
    if (!fs.existsSync(src)) { log(`  (skip ${item} - not present)`); continue; }
    copyFiltered(src, path.join(FRAMEWORK, item));
  }

  // 5. prove the bundle stands alone: the embedded python imports the stack
  log("smoke test: embedded interpreter imports the framework deps…");
  execFileSync(path.join(RUNTIME, "python.exe"),
    ["-c", "import discord, anthropic, aiohttp, aiosqlite, yaml, PIL, dotenv; print('bundle ok')"],
    { stdio: "inherit", cwd: BUNDLE });

  log("bundle staged: app/bundle/{runtime,framework}");
})().catch((e) => { console.error(e.message || e); process.exit(1); });
