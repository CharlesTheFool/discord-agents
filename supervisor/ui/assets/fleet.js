/* Fleet dashboard — dense, color-telegraphed board. */

import {
  apiGet, apiSend,
  clockTime, agoInWords, uptimeInWords, tokens, modelShort, esc,
} from "./app.js";

let bots = [];
let supervisor = null;

/* ---------- helpers ---------- */

// compact "3m ago" / "19m ago" / "Jun 9"
function shortAgo(iso) {
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
  return bot.running
    ? `<span class="chip on"><span class="led"></span>Running</span>`
    : `<span class="chip off"><span class="led"></span>Stopped</span>`;
}

function opsFor(bot) {
  if (bot.pending) return `<button disabled>…</button>`;
  if (bot.running)
    return `<button class="stop" data-act="stop">Stop</button>` +
           `<button data-act="restart">Restart</button>` +
           `<button class="danger" disabled title="stop the bot first">Delete</button>`;
  return `<button class="go" data-act="start">Start</button>` +
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
      <div class="status">${statusChip(bot)}</div>
      <div class="id">
        <a href="bot.html?id=${encodeURIComponent(bot.bot_id)}">${esc(bot.bot_id)}</a>
        <span class="pid">${bot.running ? "PID " + bot.pid : "—"}</span>
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
    <div class="gb spacer"></div>
    <div class="gb">
      <span class="gk">Last poll</span>
      <span class="gv">${now.toTimeString().slice(0, 8)}</span>
    </div>`;
}

function renderAll() { renderBoard(); renderGlobalBar(); }

/* ---------- actions ---------- */

const findBot = (id) => bots.find((b) => b.bot_id === id);

async function act(id, action) {
  const bot = findBot(id);
  bot.pending = action;
  renderAll();
  await apiSend("POST", `/api/bots/${id}/${action}`);
  delete bot.pending;
  if (action === "stop") Object.assign(bot, { running: false, pid: null, uptime_s: 0 });
  else Object.assign(bot, {
    running: true, pid: 20000 + Math.floor(Math.random() * 9999),
    uptime_s: 5, last_activity: new Date().toISOString(),
  });
  renderAll();
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
    bots.push({
      bot_id: id, running: false, pid: null, uptime_s: 0,
      model: "claude-sonnet-4-6", servers: 0,
      last_activity: new Date().toISOString(),
      messages_today: 0, last_channel: null, episodes: 0, activity_7d: [0, 0, 0, 0, 0, 0, 0],
    });
    renderAll();
  };
}

/* ---------- wiring ---------- */

document.getElementById("board").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest(".row").dataset.bot;
  const action = btn.dataset.act;
  if (action === "delete") deleteBot(id);
  else act(id, action);
});

document.getElementById("add-bot").addEventListener("click", (e) => {
  e.preventDefault();
  createBot();
});

(async function init() {
  [bots, supervisor] = await Promise.all([
    apiGet("/api/bots"),
    apiGet("/api/supervisor"),
  ]);
  renderAll();
  setInterval(renderGlobalBar, 30_000);
})();
