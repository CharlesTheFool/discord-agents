// The app's own settings (0.15.0) under plain node: Electron's pieces are fakes.
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { AppSettings, loadSettings, DEFAULTS } = require("../settings");

function rig({ packaged = true, saved } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "da-settings-"));
  const file = path.join(dir, "app-settings.json");
  if (saved !== undefined) fs.writeFileSync(file, saved);
  const blocker = { held: new Set(), next: 1,
    start(kind) { assert.strictEqual(kind, "prevent-app-suspension"); const id = this.next++; this.held.add(id); return id; },
    stop(id) { this.held.delete(id); } };
  const login = { openAtLogin: false };
  const app = { isPackaged: packaged, getLoginItemSettings: () => ({ ...login }),
                setLoginItemSettings: (s) => Object.assign(login, s) };
  return { file, blocker, login, settings: new AppSettings({ file, app, powerSaveBlocker: blocker }) };
}

const tests = {
  "defaults are off"() {
    const { settings, blocker } = rig();
    settings.apply();
    assert.deepStrictEqual(settings.view(), { keepAwake: false, startAtLogin: false, loginSupported: true });
    assert.strictEqual(blocker.held.size, 0);
  },
  "keep awake holds one blocker and lets it go"() {
    const { settings, blocker, file } = rig();
    settings.set("keepAwake", true);
    settings.apply();
    assert.strictEqual(blocker.held.size, 1);
    assert.strictEqual(loadSettings(file).keepAwake, true);
    settings.set("keepAwake", false);
    assert.strictEqual(blocker.held.size, 0);
  },
  "a saved keep-awake applies at start"() {
    const { settings, blocker } = rig({ saved: JSON.stringify({ keepAwake: true }) });
    settings.apply();
    assert.strictEqual(blocker.held.size, 1);
  },
  "start at login is the OS's login item"() {
    const { settings, login } = rig();
    assert.strictEqual(settings.set("startAtLogin", true).startAtLogin, true);
    assert.strictEqual(login.openAtLogin, true);
    login.openAtLogin = false;   // turned off outside the app
    assert.strictEqual(settings.view().startAtLogin, false);
  },
  "a dev run never registers a login item"() {
    const { settings, login } = rig({ packaged: false });
    assert.throws(() => settings.set("startAtLogin", true), /installed app only/);
    assert.strictEqual(login.openAtLogin, false);
    assert.strictEqual(settings.view().loginSupported, false);
  },
  "only known keys and booleans"() {
    const { settings } = rig();
    assert.throws(() => settings.set("shell", true));
    assert.throws(() => settings.set("keepAwake", "yes"));
  },
  "junk on disk falls back to the defaults"() {
    assert.deepStrictEqual(rig({ saved: "{not json" }).settings.settings, DEFAULTS);
    assert.deepStrictEqual(rig({ saved: JSON.stringify({ keepAwake: "on", other: 1 }) }).settings.settings,
                           DEFAULTS);
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log(`ok - ${name}`); } catch (e) { failed++; console.log(`not ok - ${name}\n  ${e.stack}`); }
}
if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log(`${Object.keys(tests).length} passed`);
