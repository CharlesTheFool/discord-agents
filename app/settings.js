// The app's own settings (spec 8.2, 0.15.0): keep the computer awake while
// the app is open, and open the app at sign-in. Stored in the app's userData;
// start-at-login's truth is the OS's own login item, read back each time.
// Electron is passed in, so this runs under plain node in tests.

const fs = require("fs");

const DEFAULTS = { keepAwake: false, startAtLogin: false };
const KEYS = Object.keys(DEFAULTS);

function loadSettings(file) {
  try {
    const saved = JSON.parse(fs.readFileSync(file, "utf-8"));
    return { ...DEFAULTS, ...Object.fromEntries(KEYS.filter((k) => typeof saved[k] === "boolean")
                                                    .map((k) => [k, saved[k]])) };
  } catch (e) {
    return { ...DEFAULTS };
  }
}

function saveSettings(file, settings) {
  fs.writeFileSync(file, JSON.stringify(settings, null, 1));
}

// One owner of the side effects: applying a settings object makes the
// world match it (the power-save blocker held or released, the login item
// registered or removed). Start at login only means something for the
// installed app: a dev run would register electron.exe.
class AppSettings {
  constructor({ file, app, powerSaveBlocker, log = () => {} }) {
    this.file = file;
    this.app = app;
    this.blocker = powerSaveBlocker;
    this.log = log;
    this.blockerId = null;
    this.settings = loadSettings(file);
  }

  get loginSupported() { return this.app.isPackaged; }

  view() {
    const atLogin = this.loginSupported && this.app.getLoginItemSettings().openAtLogin;
    return { keepAwake: this.settings.keepAwake, startAtLogin: Boolean(atLogin),
             loginSupported: this.loginSupported };
  }

  apply() {
    if (this.settings.keepAwake && this.blockerId === null) {
      // prevent-app-suspension: the PC stays up, the screen may still sleep
      this.blockerId = this.blocker.start("prevent-app-suspension");
      this.log("keep awake: on");
    } else if (!this.settings.keepAwake && this.blockerId !== null) {
      this.blocker.stop(this.blockerId);
      this.blockerId = null;
      this.log("keep awake: off");
    }
  }

  set(key, value) {
    if (!KEYS.includes(key) || typeof value !== "boolean") throw new Error(`no setting ${key}=${value}`);
    if (key === "startAtLogin") {
      if (!this.loginSupported) throw new Error("start at login works in the installed app only");
      this.app.setLoginItemSettings({ openAtLogin: value });
      this.log(`start at login: ${value ? "on" : "off"}`);
    }
    this.settings = { ...this.settings, [key]: value };
    saveSettings(this.file, this.settings);
    this.apply();
    return this.view();
  }
}

module.exports = { AppSettings, loadSettings, DEFAULTS };
