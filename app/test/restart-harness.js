// End-to-end check that the app's "Restart now" (a launcher update) stops
// its bots cleanly (0.15.7). Accepting the update quits through app.quit(),
// which skips window-all-closed; before 0.15.7 that skipped shutdownDaemon
// too, the daemon died with the app, and its bots ran on with nothing
// managing them. And a bot the daemon did stop was killed outright, so its
// next start wrote a crash marker into every channel.
//
// Runs the real main.js under Electron against this checkout (spare port,
// scratch userData), starts one bot, announces a downloaded update once the
// bot is up, answers "Restart now", and stands in for the installer. Then,
// from outside the dead app: nothing it started outlived it, the bot shut
// down cleanly (its running flag is gone), and the bot is still wanted, so
// the next launch brings it back.
//
//   node test/restart-harness.js <bot_id>      (from app/; the bot logs in to Discord)
//
// Exit 0 = pass. Leftover processes are killed and the supervisor state and
// running flag put back; the bot's own logs and messages stay, as after any
// run. The app's log is in the da-restart-* folder printed at the end.

const { spawn, spawnSync, execFileSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const PORT = "8643";   // an installed app's daemon holds 8642
const runningFlag = (botId) => path.join(ROOT, "persistence", `${botId}_running.flag`);

function api(method, route, token) {
  return new Promise((resolve) => {
    const req = http.request(`http://127.0.0.1:${PORT}${route}`,
      { method, headers: token ? { "X-DA-Token": token } : {} }, (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => { body += chunk; });
        res.on("end", () => resolve({ status: res.statusCode, body }));
      });
    req.on("error", () => resolve({ status: 0, body: "" }));
    req.end();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- inside Electron: the app under test -------------------------------------

function runApp(botId, userData) {
  const { app, dialog } = require("electron");
  process.env.SLH_SUPERVISOR_PORT = PORT;   // main.js reads it at load; the daemon inherits it
  app.setPath("userData", userData);
  fs.writeFileSync(path.join(userData, "app-config.json"), JSON.stringify({
    root: ROOT, python: path.join(ROOT, ".venv", "Scripts", "python.exe"), autoUpdateFramework: false }));

  // "Restart now" on the update dialog, the safe answer everywhere else
  dialog.showMessageBox = async (...args) => {
    const opts = args[args.length - 1];
    return { response: /^Version .* is ready\.$/.test(opts.message) ? 0 : opts.cancelId ?? 0 };
  };
  dialog.showErrorBox = (title, text) => { console.log(`error box: ${text}`); app.exit(3); };

  const { autoUpdater } = require("electron-updater");
  autoUpdater.checkForUpdates = async () => null;
  // BaseUpdater.quitAndInstall minus the installer: it quits through app.quit()
  autoUpdater.quitAndInstall = () => {
    fs.writeFileSync(path.join(userData, "quit-and-install"), new Date().toISOString());
    setImmediate(() => app.quit());
  };

  require("../main.js");
  app.whenReady().then(async () => {
    let token = null;
    while (!token) {   // main.js is bringing the daemon up meanwhile
      await sleep(1000);
      token = ((await api("GET", "/")).body.match(/<meta name="da-token" content="([^"]+)"/) || [])[1];
    }
    const since = Date.now();
    const started = await api("POST", `/api/bots/${botId}/start`, token);
    console.log(`start ${botId}: ${started.status} ${started.body}`);
    if (started.status !== 200) app.exit(6);
    // Up = booted: the bot writes its running flag at the end of its boot
    while (!(fs.existsSync(runningFlag(botId)) && fs.statSync(runningFlag(botId)).mtimeMs > since)) {
      await sleep(1000);
    }
    console.log("bot up; announcing a downloaded update");
    autoUpdater.emit("update-downloaded", { version: "9.9.9" });
  }).catch((e) => { console.log(e.stack); app.exit(4); });
  setTimeout(() => { console.log("timed out"); app.exit(5); }, 180000);
}

// --- outside: run it, then look at what's left -------------------------------

// This checkout's daemon and bot processes, by command line
function leftovers(botId) {
  const out = execFileSync("powershell", ["-NoProfile", "-Command",
    "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | " +
    "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"], { encoding: "utf8" });
  const norm = (s) => (s || "").replace(/"/g, "").toLowerCase();
  const ours = [`${ROOT}\\bot_manager.py spawn ${botId}`, `${ROOT}\\supervisor.py`]
    .map((cmd) => new RegExp(`${norm(cmd).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(\\s|$)`));
  return [].concat(out.trim() ? JSON.parse(out) : [])
    .filter((p) => ours.some((cmd) => cmd.test(norm(p.CommandLine))));
}

async function main() {
  const botId = process.argv[2];
  if (!botId) { console.log("usage: node test/restart-harness.js <bot_id>"); process.exit(2); }
  if (fs.existsSync(path.join(__dirname, "..", "app-config.json"))) {
    console.log("app/app-config.json points the app elsewhere; move it aside first");
    process.exit(2);
  }
  if (leftovers(botId).length) { console.log(`${botId} or a daemon is already running here`); process.exit(2); }

  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "da-restart-"));
  const devFeed = path.join(__dirname, "..", "dev-app-update.yml");   // opts a dev run into the updater
  const statePath = path.join(ROOT, "persistence", "supervisor_state.json");
  const flag = runningFlag(botId);
  const savedState = fs.existsSync(statePath) ? fs.readFileSync(statePath) : null;
  const flagBefore = fs.existsSync(flag);
  const restore = () => {
    fs.rmSync(devFeed, { force: true });
    if (savedState) fs.writeFileSync(statePath, savedState);
    else fs.rmSync(statePath, { force: true });
    if (!flagBefore) fs.rmSync(flag, { force: true });
  };
  process.on("SIGINT", () => { restore(); process.exit(130); });

  const failures = [];
  fs.copyFileSync(path.join(__dirname, "dev-app-update.yml"), devFeed);
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  fs.writeFileSync(statePath, JSON.stringify({ desired_running: [] }));   // the one bot we start, no others
  try {
    const log = fs.openSync(path.join(userData, "electron.log"), "w");
    const code = await new Promise((resolve) =>
      spawn(require("electron"), [__filename, botId, userData], { stdio: ["ignore", log, log] })
        .on("exit", resolve));
    console.log(`app exited ${code}; logs in ${userData}`);
    if (!fs.existsSync(path.join(userData, "quit-and-install"))) {
      failures.push("the app never got to install the update");
    }
    let left = leftovers(botId);
    for (let i = 0; i < 20 && left.length; i++) {   // a bot may still be shutting down
      await sleep(1000);
      left = leftovers(botId);
    }
    for (const p of left) {
      failures.push(`outlived the app: pid ${p.ProcessId} ${p.CommandLine}`);
      spawnSync("taskkill", ["/PID", String(p.ProcessId), "/F"]);
    }
    if (fs.existsSync(flag)) {
      failures.push(`${botId} didn't shut down cleanly: its running flag is still there (its next start reads as a crash)`);
    }
    if (!JSON.parse(fs.readFileSync(statePath, "utf8")).desired_running.includes(botId)) {
      failures.push(`${botId} is no longer wanted: the next launch won't start it`);
    }
  } finally {
    restore();
  }
  console.log(failures.length ? `FAIL\n  ${failures.join("\n  ")}` : "PASS");
  process.exit(failures.length ? 1 : 0);
}

if (process.versions.electron) runApp(...process.argv.slice(-2));
else main();
