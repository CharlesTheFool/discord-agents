// Discord Agents desktop shell (v0.9). Thin by design: find or spawn the
// supervisor daemon, open its dashboard, kill what we spawned on quit.
// All app logic lives in the web UI the daemon serves.

const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const http = require("http");

const PORT = process.env.SLH_SUPERVISOR_PORT || 8642;
const BASE = `http://127.0.0.1:${PORT}`;
let daemon = null;

function readConfig() {
  // app-config.json beside the executable (or the project in dev):
  // { "root": "C:\\path\\to\\install", "python": "python" }
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
  if (await daemonUp()) return true; // operator already runs one - attach
  const cfg = readConfig();
  const script = path.join(cfg.root, "supervisor.py");
  if (!fs.existsSync(script)) {
    dialog.showErrorBox(
      "Discord Agents",
      `No supervisor.py found at ${cfg.root}.\n` +
      "Point app-config.json's \"root\" at your Discord Agents install.");
    return false;
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

async function createWindow() {
  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    autoHideMenuBar: true,
    title: "Discord Agents",
  });
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
