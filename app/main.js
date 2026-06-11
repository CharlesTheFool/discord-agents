// Discord Agents desktop shell (v0.9). Thin by design: find or spawn the
// supervisor daemon, open its dashboard, kill what we spawned on quit.
// All app logic lives in the web UI the daemon serves.
//
// Auto-update has two layers (v0.9.1): electron-updater keeps this launcher
// current from GitHub Releases, and updater.js fast-forwards the framework
// checkout to the latest release before the daemon starts on the new code.

const { app, BrowserWindow, dialog } = require("electron");
const { autoUpdater } = require("electron-updater");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const http = require("http");
const { updateFrameworkRepo } = require("./updater");

const PORT = process.env.SLH_SUPERVISOR_PORT || 8642;
const BASE = `http://127.0.0.1:${PORT}`;
const UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000; // re-check the launcher every 6h
let daemon = null;

// Append-only log beside the rest of the app's state; also mirrored to stdout
// for dev runs. Doubles as electron-updater's logger.
const logFile = path.join(app.getPath("userData"), "app.log");
function log(msg) {
  const line = `${new Date().toISOString()} ${msg}\n`;
  try { fs.appendFileSync(logFile, line); } catch (e) { /* best effort */ }
  process.stdout.write(line);
}
const updaterLogger = { info: log, warn: log, error: log, debug: () => {} };

function readConfig() {
  // app-config.json beside the executable (or the project in dev):
  // { "root": "C:\\path\\to\\install", "python": "python" }
  // Optional: "autoUpdateFramework": false, "frameworkBranch": "main".
  const candidates = [
    path.join(path.dirname(app.getPath("exe")), "app-config.json"),
    path.join(__dirname, "app-config.json"),
  ];
  for (const p of candidates) {
    try {
      return JSON.parse(fs.readFileSync(p, "utf-8"));
    } catch (e) { /* next */ }
  }
  return { root: path.join(__dirname, ".."), python: "python" };
}

function daemonUp() {
  return new Promise((resolve) => {
    const req = http.get(`${BASE}/api/supervisor`, (res) => {
      resolve(res.statusCode === 200);
      res.resume();
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1500, () => { req.destroy(); resolve(false); });
  });
}

async function ensureDaemon() {
  if (await daemonUp()) return true; // operator already runs one - attach, leave it be
  const cfg = readConfig();
  const script = path.join(cfg.root, "supervisor.py");
  if (!fs.existsSync(script)) {
    dialog.showErrorBox(
      "Discord Agents",
      `No supervisor.py found at ${cfg.root}.\n` +
      "Point app-config.json's \"root\" at your Discord Agents install.");
    return false;
  }
  // Catch the framework up to the latest release before it boots. Skipped on
  // dev branches, dirty trees, and non-git installs (see updater.js).
  if (cfg.autoUpdateFramework !== false) {
    const r = updateFrameworkRepo({
      root: cfg.root,
      python: cfg.python || "python",
      branch: cfg.frameworkBranch || "main",
      log,
    });
    log(`framework update: ${r.status}${r.reason ? ` (${r.reason})` : ""}` +
        (r.status === "updated" ? ` ${r.from.slice(0, 7)}→${r.to.slice(0, 7)}` : ""));
  }
  daemon = spawn(cfg.python || "python", [script, "--root", cfg.root],
                 { cwd: cfg.root, stdio: "ignore" });
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await daemonUp()) return true;
  }
  dialog.showErrorBox("Discord Agents",
                      "The supervisor daemon didn't come up - check logs.");
  return false;
}

// electron-updater pulls the new installer from GitHub Releases, stages it,
// and offers a restart. Only meaningful for packaged builds; a dev-app-update.yml
// beside main.js opts a dev run in for local testing.
function setupShellAutoUpdate(win) {
  const devFeed = fs.existsSync(path.join(__dirname, "dev-app-update.yml"));
  if (!app.isPackaged && !devFeed) return;

  autoUpdater.logger = updaterLogger;
  autoUpdater.allowPrerelease = true;   // releases ship as GitHub pre-releases
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  if (!app.isPackaged) autoUpdater.forceDevUpdateConfig = true;

  autoUpdater.on("update-available", (info) => log(`launcher update available: ${info.version}`));
  autoUpdater.on("update-not-available", () => log("launcher up to date"));
  autoUpdater.on("error", (err) => log(`launcher update error: ${err == null ? "unknown" : err.message || err}`));
  autoUpdater.on("update-downloaded", async (info) => {
    const { response } = await dialog.showMessageBox(win, {
      type: "info",
      buttons: ["Restart now", "Later"],
      defaultId: 0,
      cancelId: 1,
      title: "Discord Agents",
      message: `Version ${info.version} is ready.`,
      detail: "Restart to finish updating. Your bots, config, and memories are untouched.",
    });
    if (response === 0) autoUpdater.quitAndInstall();
  });

  autoUpdater.checkForUpdates().catch((e) => log(`launcher update check failed: ${e.message}`));
  setInterval(() => {
    autoUpdater.checkForUpdates().catch((e) => log(`launcher update check failed: ${e.message}`));
  }, UPDATE_INTERVAL_MS);
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    autoHideMenuBar: true,
    title: "Discord Agents",
  });
  setupShellAutoUpdate(win);
  if (await ensureDaemon()) {
    await win.loadURL(BASE);
  } else {
    await win.loadFile(path.join(__dirname, "placeholder.html"));
  }
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (daemon) daemon.kill();
  app.quit();
});
