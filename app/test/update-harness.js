// Headless end-to-end check of the launcher auto-update path.
// Runs the real electron-updater against a local generic feed serving a
// newer build (0.9.2) than this app's version (0.9.1), and asserts it
// detects, downloads, and hash-verifies the update.
//
//   FEED_PORT=8799 npx electron test/update-harness.js
//
// Exit 0 = downloaded & verified 0.9.2. Non-zero = failure (see label).

const { app } = require("electron");

// Running a script path directly doesn't bind app/package.json as the manifest,
// so app.getVersion() would report Electron's own version. Pin it to the
// shipped version so the updater compares 0.9.1 (installed) vs 0.9.2 (feed).
app.getVersion = () => "0.9.1";

const { autoUpdater } = require("electron-updater");

app.disableHardwareAcceleration();

const PORT = process.env.FEED_PORT || "8799";
let done = false;
function finish(code, label) {
  if (done) return;
  done = true;
  console.log(`RESULT ${code} ${label}`);
  setTimeout(() => app.exit(code), 150);
}

app.whenReady().then(async () => {
  console.log(`app version = ${app.getVersion()}`);
  autoUpdater.forceDevUpdateConfig = true; // run though we're not packaged
  autoUpdater.allowPrerelease = true;
  autoUpdater.autoDownload = true;
  autoUpdater.logger = console;
  autoUpdater.setFeedURL({ provider: "generic", url: `http://127.0.0.1:${PORT}/` });

  autoUpdater.on("update-available", (i) => console.log(`AVAILABLE ${i.version}`));
  autoUpdater.on("update-not-available", (i) => finish(2, `not-available ${i && i.version}`));
  autoUpdater.on("error", (e) => finish(3, `error ${e && e.message}`));
  autoUpdater.on("update-downloaded", (i) =>
    finish(i.version === "0.9.2" ? 0 : 4, `downloaded ${i.version}`));

  try {
    await autoUpdater.checkForUpdates();
  } catch (e) {
    finish(5, `check-threw ${e.message}`);
  }
  setTimeout(() => finish(6, "timeout"), 90000);
});
