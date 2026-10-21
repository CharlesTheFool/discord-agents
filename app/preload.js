// The one bridge from the dashboard page to the app (0.15.0): its own two
// settings, nothing else. The page runs isolated; main checks every call's
// origin before it answers.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("discordAgentsApp", {
  getSettings: () => ipcRenderer.invoke("da-settings:get"),
  setSetting: (key, value) => ipcRenderer.invoke("da-settings:set", key, value),
});
