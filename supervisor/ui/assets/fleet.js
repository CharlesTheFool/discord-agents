/* Fleet dashboard — dense, color-telegraphed board. */

import {
  apiGet, apiSend, apiFetch,
  clockTime, agoInWords, uptimeInWords, tokens, modelShort, esc,
} from "./app.js";

let bots = [];
let supervisor = null;
let setup = null;

/* ---------- helpers ---------- */

// compact "3m ago" / "19m ago" / "Jun 9"
function shortAgo(iso) {
  if (!iso) return "never";   // a bot with no messages yet (new Date(null) is 1970)
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 90) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function volBars(arr) {
  const max = Math.max(1, ...arr);
  return arr.map((v, i) =>
    `<i class="${i === arr.length - 1 ? "last" : ""}" style="height:${Math.round((v / max) * 100)}%"></i>`).join("");
}

/* ---------- rendering ---------- */

function statusChip(bot) {
  if (bot.pending)
    return `<span class="chip ${bot.pending === "stop" ? "off" : "on"}"><span class="led"></span>${
      bot.pending === "start" ? "Starting" : bot.pending === "stop" ? "Stopping" : "Restarting"}…</span>`;
  if (bot.running) return `<span class="chip on"><span class="led"></span>Running</span>`;
  return bot.workshop?.start_blocked
    ? `<span class="chip blocked" title="${esc(bot.workshop.start_blocked)}"><span class="led"></span>Can't start</span>`
    : `<span class="chip off"><span class="led"></span>Stopped</span>`;
}

function opsFor(bot) {
  if (bot.pending) return `<button disabled>…</button>`;
  if (bot.running)
    return `<button class="stop" data-act="stop">Stop</button>` +
           `<button data-act="restart">Restart</button>` +
           `<button class="danger" disabled title="stop the bot first">Delete</button>`;
  const blocked = bot.workshop?.start_blocked;
  const start = blocked
    ? `<button class="go" data-act="start" title="${esc(blocked)}">Try again</button>`
    : `<button class="go" data-act="start">Start</button>`;
  return start +
         `<button disabled>Restart</button>` +
         `<button class="danger" data-act="delete">Delete</button>`;
}

function modelFamily(model) {
  if (model.includes("haiku")) return "haiku";
  if (model.includes("opus")) return "opus";
  return "sonnet";
}

function signalsHTML(bot) {
  if (!bot.running) return `<span class="quiet">—</span>`;
  const out = [];
  const pct = bot.context_pct || 0;
  const ctxCls = pct >= 90 ? "hot" : pct >= 80 ? "warn" : "";
  out.push(`<span class="sig ctx ${ctxCls}"><span class="lbl">ctx</span>
    <span class="mini"><i style="width:${pct}%"></i></span>${pct}%</span>`);
  if (bot.followups) out.push(`<span class="sig fu"><span class="lbl">fu</span>${bot.followups}</span>`);
  if (bot.dms) out.push(`<span class="sig prime"><span class="lbl">dm</span>${bot.dms}</span>`);
  return out.join("");
}

function rowHTML(bot) {
  const cls = bot.running || bot.pending === "stop" ? "running" : "stopped";
  const seen = bot.running ? esc(bot.last_channel || "—") : "idle";
  return `
    <div class="row ${cls}" data-bot="${esc(bot.bot_id)}">
      <div class="edge"></div>
      <div class="id">
        <a class="botname" href="bot.html?id=${encodeURIComponent(bot.bot_id)}">${esc(bot.bot_id)}</a>
        <div class="substat">${statusChip(bot)}<span class="pid">${bot.running ? "PID " + bot.pid : "idle"}${
          bot.workshop?.state === "off" ? " · workshop off" : ""}</span></div>
      </div>
      <div><span class="tag ${modelFamily(bot.model)}">${esc(modelShort(bot.model))}</span></div>
      <div class="num">${bot.servers} <span class="u">srv</span></div>
      <div class="activity">
        <div class="seen"><b>${shortAgo(bot.last_activity)}</b> · ${seen}</div>
        <div class="today"><b>${bot.messages_today}</b> msgs today</div>
      </div>
      <div class="vol">${volBars(bot.activity_7d || [])}</div>
      <div class="signals">${signalsHTML(bot)}</div>
      <div class="ops">${opsFor(bot)}</div>
    </div>`;
}

function renderBoard() {
  document.getElementById("board").innerHTML = bots.map(rowHTML).join("");
  const running = bots.filter((b) => b.running).length;
  document.getElementById("fleet-count").textContent =
    `${bots.length} bot${bots.length !== 1 ? "s" : ""} · ${running} running`;
}

function renderGlobalBar() {
  const now = new Date();
  const running = bots.filter((b) => b.running).length;
  const t = supervisor.tokens_today;
  const down = !supervisor.online;
  document.getElementById("notices").innerHTML = noticesHTML(supervisor.app);
  document.getElementById("globalbar").innerHTML = `
    <div class="gb supervisor ${down ? "down" : ""}">
      <span class="gk">Supervisor</span>
      <span class="gv"><span class="led"></span>${down ? "Offline" : "Online"}</span>
    </div>
    <div class="gb">
      <span class="gk">Bots running</span>
      <span class="gv"><span class="up">${running}</span><span class="tot"> / ${bots.length}</span></span>
    </div>
    <div class="gb">
      <span class="gk">Tokens read today</span>
      <span class="gv">${tokens(t.cache_read)}</span>
    </div>
    <div class="gb">
      <span class="gk">In · Out</span>
      <span class="gv">${tokens(t.uncached_in)} · ${tokens(t.out)}</span>
    </div>
    <div class="gb">
      <span class="gk">Cost today</span>
      <span class="gv">≈ $${(supervisor.cost_today_usd || 0).toFixed(2)}${
        supervisor.cost_complete === false ? `<span class="tot"> (partial)</span>` : ""}</span>
    </div>
    <div class="gb spacer"></div>
    <div class="gb key ${setup && !setup.anthropic_key_set ? "warn" : ""}" id="gb-key" title="Claude API key">
      <span class="gk">Claude key</span>
      <span class="gv">${setup ? (setup.anthropic_key_set ? "set ✓" : "not set — click") : "…"}</span>
    </div>
    <div class="gb">
      <span class="gk">Last poll</span>
      <span class="gv">${now.toTimeString().slice(0, 8)}</span>
    </div>`;
  document.getElementById("gb-key")?.addEventListener("click", openKeyDialog);
}

function openKeyDialog() {
  const dlg = document.getElementById("dlg-key");
  const input = document.getElementById("key-in");
  const err = document.getElementById("key-err");
  input.value = ""; err.style.display = "none";
  dlg.showModal();
  dlg.querySelector("form").onsubmit = async (e) => {
    e.preventDefault();
    const key = input.value.trim();
    if (!key) { dlg.close(); return; }
    err.style.display = "none";
    const btn = dlg.querySelector(".btn.primary");
    btn.disabled = true; btn.textContent = "Checking…";
    try {
      const r = await apiFetch("/api/setup/anthropic", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || `validation failed (${r.status})`);
      setup = await apiGet("/api/setup");
      dlg.close();
      renderGlobalBar();
    } catch (ex) {
      err.textContent = ex.message; err.style.display = "block";
    }
    btn.disabled = false; btn.textContent = "Validate & save";
  };
}

function renderAll() { renderBoard(); renderGlobalBar(); }

/* ---------- actions ---------- */

const findBot = (id) => bots.find((b) => b.bot_id === id);

async function refreshBots() {
  bots = await apiGet("/api/bots");
  renderAll();
}

async function act(id, action) {
  const bot = findBot(id);
  bot.pending = action;
  renderAll();
  try { await apiSend("POST", `/api/bots/${id}/${action}`); }
  catch (ex) { /* the re-pull below shows what actually happened */ }
  delete bot.pending;
  await refreshBots();
  // a start test-boots the workshop first: running, or refused, lands seconds later
  if (action !== "stop") [3000, 8000, 15000].forEach((ms) => setTimeout(refreshBots, ms));
}

// What the app reported about its own update and dependencies (app/main.js
// writes it; the daemon serves it on /api/supervisor), plus a pending restart.
function noticesHTML(app) {
  if (!app) return "";
  const out = [];
  if (app.restart_pending)
    out.push("Restart Windows to finish turning on virtualization: your bots' workshops need it.");
  const fw = app.framework || {};
  if (fw.status === "skipped" && fw.files)
    out.push(`Updates are paused: ${fw.files.map(esc).join(", ")} ${fw.files.length === 1 ? "was" : "were"} ` +
             `changed locally. Undo or commit the change to resume updates.`);
  else if (fw.status === "skipped" && /diverged/.test(fw.reason || ""))
    out.push("Updates are paused: this install's history diverged from the release branch.");
  else if (fw.status === "error")
    out.push(`The last update check failed: ${esc(fw.reason || "unknown error")}.`);
  if (app.deps && app.deps.status === "error")
    out.push(`Installing dependencies failed (${esc(app.deps.reason || "unknown error")}). ` +
             `The app tries again on its next launch.`);
  return out.map((text) => `<div class="notice">${text}</div>`).join("");
}

async function deleteBot(id) {
  const dlg = document.getElementById("dlg-delete");
  document.getElementById("del-name").textContent = id;
  dlg.showModal();
  dlg.querySelector(".confirm").onclick = async () => {
    dlg.close();
    await apiSend("DELETE", `/api/bots/${id}`);
    bots = bots.filter((b) => b.bot_id !== id);
    renderAll();
  };
}

function createBot() {
  const dlg = document.getElementById("dlg-create");
  const input = document.getElementById("new-id");
  const err = dlg.querySelector(".field-error");
  input.value = ""; err.style.display = "none";
  dlg.showModal();
  dlg.querySelector("form").onsubmit = async (e) => {
    e.preventDefault();
    const id = input.value.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9-]{1,31}$/.test(id) || findBot(id)) {
      err.textContent = findBot(id)
        ? `a bot named "${id}" already exists`
        : "ids are 2–32 chars: lowercase letters, digits, hyphens";
      err.style.display = "block";
      return;
    }
    dlg.close();
    await apiSend("POST", "/api/bots", { bot_id: id, template: document.getElementById("new-template").value });
    // straight to its Configure tab - token + servers live there now
    location.href = `bot.html?id=${encodeURIComponent(id)}#configure`;
  };
}

/* ---------- wiring ---------- */

document.getElementById("board").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (btn) {
    const id = btn.closest(".row").dataset.bot;
    const action = btn.dataset.act;
    if (action === "delete") deleteBot(id);
    else act(id, action);
    return;
  }
  // anywhere else on the card opens that bot's dashboard
  const row = e.target.closest(".row");
  if (row) location.href = `bot.html?id=${encodeURIComponent(row.dataset.bot)}`;
});

document.getElementById("add-bot").addEventListener("click", (e) => {
  e.preventDefault();
  createBot();
});

(async function init() {
  [bots, supervisor, setup] = await Promise.all([
    apiGet("/api/bots"),
    apiGet("/api/supervisor"),
    apiGet("/api/setup").catch(() => null),
  ]);
  document.getElementById("masthead-mark").textContent =
    `Supervisor · v${supervisor.version}`;
  document.getElementById("foot-host").textContent =
    `LOCALHOST:${supervisor.port || ""}`;
  document.getElementById("foot-version").textContent =
    `supervisor v${supervisor.version}`;
  renderAll();
  renderAppSettings();
  setInterval(renderGlobalBar, 30_000);
  // first-run onboarding: the install can't think without a Claude key
  if (setup && !setup.anthropic_key_set) openKeyDialog();
})();

/* ---------- this computer (0.15.0): the app's own settings ---------- */
/* Only inside the desktop app: its preload exposes window.discordAgentsApp. */

const APP_TOGGLES = [
  ["keepAwake", "Keep this computer awake while Discord Agents is open",
   "Bots can't answer from a sleeping PC. The screen may still turn off."],
  ["startAtLogin", "Open Discord Agents when you sign in",
   "Your bots come back on their own after a restart."],
];

async function renderAppSettings() {
  const bridge = window.discordAgentsApp;
  const el = document.getElementById("app-settings");
  if (!bridge || !el) return;
  const s = await bridge.getSettings();
  if (!s) return;
  el.innerHTML = `<h3>This computer</h3>` + APP_TOGGLES.map(([key, label, help]) => {
    const off = key === "startAtLogin" && !s.loginSupported;
    return `<label class="apptoggle ${off ? "off" : ""}">
      <input type="checkbox" data-key="${key}" ${s[key] ? "checked" : ""} ${off ? "disabled" : ""}>
      <span><b>${esc(label)}</b><br><span class="help">${esc(off ? "Works in the installed app." : help)}</span></span>
    </label>`;
  }).join("") + `<div class="field-error" id="app-settings-err"></div>`;
  el.querySelectorAll("input[data-key]").forEach((box) => box.addEventListener("change", async () => {
    try {
      await bridge.setSetting(box.dataset.key, box.checked);
      document.getElementById("app-settings-err").textContent = "";
    } catch (e) {
      box.checked = !box.checked;
      document.getElementById("app-settings-err").textContent = `Couldn't change that: ${e.message}`;
    }
  }));
}
