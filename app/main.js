// Discord Agents desktop shell (v0.9). Thin by design: find or spawn the
// supervisor daemon, open its dashboard, kill what we spawned on quit.
// All app logic lives in the web UI the daemon serves.
//
// Auto-update has two layers (v0.9.1): electron-updater keeps this launcher
// current from GitHub Releases, and updater.js fast-forwards the framework
// checkout to the latest release before the daemon starts on the new code.

const { app, BrowserWindow, dialog, ipcMain, powerSaveBlocker } = require("electron");
const { autoUpdater } = require("electron-updater");
const { execFile, spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const { updateFrameworkRepo, ensureDeps } = require("./updater");
const { AppSettings } = require("./settings");

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
  // Resolution order:
  //  1. app-config.json beside the executable (or the project in dev) -
  //     the git-checkout model; points at a framework install.
  //  2. Packaged bundle (resources/framework + resources/runtime) - the
  //     self-contained installer; data lives in the user's appData.
  //  3. Dev fallback: the repo this app folder sits in.
  // { "root": ..., "python": ..., "autoUpdateFramework": false, "frameworkBranch": "main" }
  // userData copy survives installer upgrades (the exe-dir copy may not)
  const candidates = [
    path.join(path.dirname(app.getPath("exe")), "app-config.json"),
    path.join(__dirname, "app-config.json"),
    path.join(app.getPath("userData"), "app-config.json"),
  ];
  for (const p of candidates) {
    try {
      return JSON.parse(fs.readFileSync(p, "utf-8"));
    } catch (e) { /* next */ }
  }
  const bundledFramework = path.join(process.resourcesPath || "", "framework");
  const bundledPython = path.join(process.resourcesPath || "", "runtime", "python", "python.exe");
  if (app.isPackaged && fs.existsSync(bundledFramework) && fs.existsSync(bundledPython)) {
    return {
      bundled: true,
      codeRoot: bundledFramework,
      python: bundledPython,
      root: app.getPath("userData"),       // %APPDATA%\Discord Agents - survives updates
      autoUpdateFramework: false,          // the installer updates the framework
    };
  }
  return { root: path.join(__dirname, ".."), python: "python" };
}

// Health check: GET /api/supervisor is the one API route the daemon leaves
// open, so this works against daemons with and without the token gate
// (the framework updates before the launcher does).
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

// The daemon gates the rest of /api/ behind a per-install token embedded in
// the dashboard page it serves (supervisor.py _render_html). Null when the
// page has none, e.g. a daemon from before the gate existed.
function fetchDaemonToken() {
  return new Promise((resolve) => {
    const req = http.get(`${BASE}/`, (res) => {
      let html = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { html += chunk; });
      res.on("end", () => {
        const m = res.statusCode === 200 && html.match(/<meta name="da-token" content="([^"]+)"/);
        resolve(m ? m[1] : null);
      });
    });
    req.on("error", () => resolve(null));
    req.setTimeout(1500, () => { req.destroy(); resolve(null); });
  });
}

// What the app tells the daemon, as a file in the data root it reads (the
// dashboard shows it): the last framework update and dependency install,
// and a restart the app is waiting on.
function appStatePath(cfg) { return path.join(cfg.root, "persistence", "app_state.json"); }
function readAppState(cfg) {
  try { return JSON.parse(fs.readFileSync(appStatePath(cfg), "utf-8")); } catch (e) { return {}; }
}
function writeAppState(cfg, state) {
  try {
    fs.mkdirSync(path.dirname(appStatePath(cfg)), { recursive: true });
    fs.writeFileSync(appStatePath(cfg), JSON.stringify(state, null, 1));
  } catch (e) { log(`couldn't write app state: ${e.message}`); }
}

async function ensureDaemon() {
  if (await daemonUp()) return true; // operator already runs one - attach, leave it be
  const cfg = readConfig();
  const codeRoot = cfg.codeRoot || cfg.root;
  const script = path.join(codeRoot, "supervisor.py");
  if (!fs.existsSync(script)) {
    dialog.showErrorBox(
      "Discord Agents",
      `No supervisor.py found at ${codeRoot}.\n` +
      "Point app-config.json's \"root\" at your Discord Agents install.");
    return false;
  }
  const state = readAppState(cfg);
  // a pending restart is over once the machine has booted since it was asked for
  if (state.restart_pending && state.restart_pending.at < Date.now() - os.uptime() * 1000) {
    delete state.restart_pending;
  }
  // Catch the framework up to the latest release before it boots, then make
  // its dependencies match. Skipped on dev branches, dirty trees, non-git
  // installs, and bundled installs (the installer carries both).
  if (cfg.autoUpdateFramework !== false && !cfg.bundled) {
    const python = cfg.python || "python";
    const r = updateFrameworkRepo({ root: cfg.root, branch: cfg.frameworkBranch || "main", log });
    log(`framework update: ${r.status}${r.reason ? ` (${r.reason})` : ""}` +
        (r.status === "updated" ? ` ${r.from.slice(0, 7)}→${r.to.slice(0, 7)}` : ""));
    const deps = ensureDeps({
      root: cfg.root, python, log,
      stamp: path.join(cfg.root, "persistence", "requirements.sha256"),
    });
    log(`dependencies: ${deps.status}${deps.reason ? ` (${deps.reason})` : ""}`);
    state.framework = { ...r, at: new Date().toISOString() };
    state.deps = deps;
  }
  writeAppState(cfg, state);
  fs.mkdirSync(cfg.root, { recursive: true }); // data root (the daemon seeds inside it)
  const args = [script, "--root", cfg.root];
  if (cfg.codeRoot) args.push("--code-root", cfg.codeRoot);
  log(`spawning daemon: ${cfg.python || "python"} ${args.join(" ")}`);
  daemon = spawn(cfg.python || "python", args,
                 { cwd: cfg.root, stdio: "ignore" });
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await daemonUp()) return true;
  }
  dialog.showErrorBox("Discord Agents",
                      "The supervisor daemon didn't come up - check logs.");
  return false;
}

// Closing the window means closing the app: bots stop cleanly (their desired
// state is kept, so they come back on next launch). Only a daemon WE spawned
// is stopped - one the operator runs from a terminal is left alone.
async function requestShutdown() {
  const token = await fetchDaemonToken();
  return new Promise((resolve) => {
    const req = http.request(`${BASE}/api/supervisor/shutdown`,
      { method: "POST", headers: token ? { "X-DA-Token": token } : {} },
      (res) => { res.resume(); resolve(res.statusCode === 200); });
    req.on("error", () => resolve(false));
    req.setTimeout(3000, () => { req.destroy(); resolve(false); });
    req.end();
  });
}

async function shutdownDaemon() {
  if (!daemon) return; // attached to an operator-run daemon - leave it be
  log("window closed - asking the daemon to stop its bots and exit");
  const accepted = await requestShutdown();
  if (accepted) {
    for (let i = 0; i < 30; i++) {           // up to 15s for clean bot stops
      await new Promise((r) => setTimeout(r, 500));
      if (!(await daemonUp())) { log("daemon stopped cleanly"); daemon = null; return; }
    }
  }
  log("graceful shutdown timed out - killing the daemon process");
  try { daemon.kill(); } catch (e) { /* already gone */ }
  daemon = null;
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

function apiJson(route, token) {
  return new Promise((resolve) => {
    const req = http.get(`${BASE}${route}`, { headers: token ? { "X-DA-Token": token } : {} }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        try { resolve(res.statusCode === 200 ? JSON.parse(body) : null); } catch (e) { resolve(null); }
      });
    });
    req.on("error", () => resolve(null));
    req.setTimeout(5000, () => { req.destroy(); resolve(null); });
  });
}

// Windows (spec 8.2): a bot whose workshop can't boot a VM there almost
// always means the Windows Hypervisor Platform feature is off. The bots'
// own test boot is the judge - never the optional-feature list, which can
// say "enabled" on a machine that can't run a VM. Asked once per launch.
async function watchForVirtualization(win, cfg) {
  if (process.platform !== "win32") return;
  const token = await fetchDaemonToken();
  for (let i = 0; i < 18; i++) {   // ~3 minutes: each bot test-boots as it starts
    await new Promise((r) => setTimeout(r, 10000));
    const bots = await apiJson("/api/bots", token);
    if ((bots || []).some((b) => b.workshop && b.workshop.kind === "boot" && b.workshop.start_blocked)) {
      return offerVirtualization(win, cfg);
    }
  }
}

function enableHypervisorPlatform() {
  // dism through a UAC prompt; 3010 = done, restart needed; 1223 = UAC declined
  const command = "try { $p = Start-Process dism.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden " +
    "-ArgumentList '/online','/enable-feature','/featurename:HypervisorPlatform','/all','/norestart'; " +
    "exit $p.ExitCode } catch { exit 1223 }";
  return new Promise((resolve) => {
    execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", command],
             { windowsHide: true }, (err) => resolve(err ? (typeof err.code === "number" ? err.code : -1) : 0));
  });
}

async function offerVirtualization(win, cfg) {
  const box = (options) => dialog.showMessageBox(win, { title: "Discord Agents", ...options });
  if (readAppState(cfg).restart_pending) {
    await box({ type: "info", buttons: ["OK"],
                message: "Restart Windows to finish turning on virtualization for your bots' workshops." });
    return;
  }
  const { response } = await box({
    type: "warning", buttons: ["Turn it on", "Not now"], defaultId: 0, cancelId: 1,
    message: "Your bots' workshops need Windows' virtualization feature, and it's off.",
    detail: "Turning it on asks for administrator permission, then a restart. If it's already on, " +
            "virtualization may be switched off in your PC's firmware (BIOS/UEFI) instead.",
  });
  if (response !== 0) return;
  const code = await enableHypervisorPlatform();
  log(`enabling HypervisorPlatform: exit ${code}`);
  if (code !== 0 && code !== 3010) {
    await box({ type: "error", buttons: ["OK"], message: "Virtualization wasn't turned on.",
                detail: code === 1223 ? "The administrator prompt was declined." : `Windows returned ${code}.` });
    return;
  }
  writeAppState(cfg, { ...readAppState(cfg), restart_pending: { reason: "virtualization", at: Date.now() } });
  const { response: when } = await box({
    type: "info", buttons: ["Restart now", "Later"], defaultId: 0, cancelId: 1,
    message: "Virtualization is on. Restart Windows to finish.",
  });
  if (when === 0) {
    await shutdownDaemon();
    execFile("shutdown.exe", ["/r", "/t", "0"], { windowsHide: true });
  }
}

// The dashboard's toggles for the app's own settings (0.15.0), through the
// preload bridge. Only pages from our daemon may ask.
function serveSettings() {
  const settings = new AppSettings({
    file: path.join(app.getPath("userData"), "app-settings.json"), app, powerSaveBlocker, log });
  settings.apply();
  const ours = (event) => (event.senderFrame?.url || "").startsWith(`${BASE}/`);
  ipcMain.handle("da-settings:get", (event) => (ours(event) ? settings.view() : null));
  ipcMain.handle("da-settings:set", (event, key, value) => {
    if (!ours(event)) throw new Error("not from the dashboard");
    return settings.set(key, value);
  });
}

async function createWindow() {
  serveSettings();
  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    autoHideMenuBar: true,
    title: "Discord Agents",
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  setupShellAutoUpdate(win);
  if (await ensureDaemon()) {
    await win.loadURL(BASE);
    watchForVirtualization(win, readConfig()).catch((e) => log(`virtualization check failed: ${e.message}`));
  } else {
    await win.loadFile(path.join(__dirname, "placeholder.html"));
  }
}

app.whenReady().then(createWindow);

app.on("window-all-closed", async () => {
  await shutdownDaemon();
  app.quit();
});
