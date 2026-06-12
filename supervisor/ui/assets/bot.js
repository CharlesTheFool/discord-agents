/* Per-bot dashboard: Monitor / Configure / Memories / Repository. */

import {
  apiGet, apiSend,
  clockTime, agoInWords, uptimeInWords, tokens, modelShort, esc,
} from "./app.js";

const botId = new URLSearchParams(location.search).get("id") || "slh-01";
const A = (p) => `/api/bots/${botId}${p}`;

/* "tomorrow 3 p.m." / "today 6 p.m." / "Jun 13 2 p.m." / "overdue" */
function relDay(iso) {
  const d = new Date(iso), now = new Date();
  if (d < now) return "overdue";
  const t = clockTime(d);
  const sameDay = (a, b) => a.getDate() === b.getDate() && a.getMonth() === b.getMonth();
  const tmr = new Date(now); tmr.setDate(now.getDate() + 1);
  if (sameDay(d, now)) return `today ${t}`;
  if (sameDay(d, tmr)) return `tomorrow ${t}`;
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${t}`;
}

/* "Jun 10 · 8:41 p.m." */
function dayClock(iso) {
  const d = new Date(iso);
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} · ${clockTime(d)}`;
}

/* ============================ shell ============================ */

async function boot() {
  document.getElementById("bot-name").textContent = botId;
  document.title = `Discord Agents — ${botId}`;

  apiGet("/api/supervisor").then((sup) => {
    document.getElementById("top-version").textContent =
      `v${sup.version} · localhost:${sup.port}`;
    document.getElementById("foot-version").textContent =
      `SUPERVISOR v${sup.version}`;
  }).catch(() => {});

  const status = await apiGet(A("/status"));
  renderNameplate(status);

  wireTabs();
  // lazy-load each tab the first time it's shown
  showTab(location.hash.replace("#", "") || "monitor");
}

function renderNameplate(s) {
  const np = document.getElementById("nameplate");
  np.classList.toggle("running", s.running);
  const serverNames = (s.servers || []).map((x) => x.name).join(" · ");
  document.getElementById("np-state").innerHTML = s.running
    ? `<span class="chip on"><span class="led"></span>Running</span>`
    : `<span class="chip off"><span class="led"></span>Stopped</span>`;
  document.getElementById("np-meta").innerHTML = s.running
    ? `PID ${s.pid} · up ${uptimeInWords(s.uptime_s)}<span class="sub">${esc(modelShort(s.model))} · ${esc(serverNames || "no servers")}</span>`
    : `<span class="sub">${esc(modelShort(s.model))} · ${esc(serverNames || "no servers")}</span>`;
  document.getElementById("np-ops").innerHTML = s.running
    ? `<button class="op">Restart</button><button class="op no">Stop</button>`
    : `<button class="op go">Start</button>`;
}

const loaded = {};
function wireTabs() {
  document.querySelectorAll(".tabs a").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      showTab(a.dataset.tab);
    });
  });
}
function showTab(tab) {
  if (!document.getElementById(`tab-${tab}`)) tab = "monitor";
  location.hash = tab;
  document.querySelectorAll(".tabs a").forEach((a) =>
    a.classList.toggle("active", a.dataset.tab === tab));
  document.querySelectorAll(".tabpanel").forEach((p) =>
    p.classList.toggle("active", p.id === `tab-${tab}`));
  if (!loaded[tab]) { loaded[tab] = true; LOADERS[tab](); }
}

/* ============================ Monitor ============================ */

let monStatus = null, monTrace = [], monMain = [], monEpisodes = [], monSkills = [];
let monView = new URLSearchParams(location.search).get("view") || "channels";

async function loadMonitor() {
  const [status, stats, trace, main, episodes, skills] = await Promise.all([
    apiGet(A("/status")),
    apiGet(A("/stats")),
    apiGet(A("/logs?file=conversations&tail=50")),
    apiGet(A("/logs?file=main&tail=50")),
    apiGet(A("/episodes")),
    apiGet(A("/skills")),
  ]);
  monStatus = status; monTrace = trace; monMain = main;
  monEpisodes = episodes; monSkills = skills;
  renderGauges(status);
  renderStatband(stats);
  renderCommitments(status);
  buildChannels();
  // honor ?view= deep-link
  document.querySelectorAll(".subtoggle a").forEach((a) => a.classList.toggle("active", a.dataset.view === monView));
  renderActivity();
  wireActivity();
}

/* ---------- commitments band (stateful special features) ---------- */

function renderCommitments(s) {
  const fu = (s.followups || []).map((f) =>
    `<div class="item">Nudge <b>${esc(f.who)}</b> — ${esc(f.about)}
       <span class="when">· fires ${relDay(f.fire_after)} · ${esc(f.channel)}</span></div>`).join("")
    || `<div class="none">none scheduled</div>`;
  const w = (s.watches || []).map((x) =>
    `<div class="item">${esc(x.question)}
       <span class="when">· watching <b>${esc(x.target)}</b> for ${esc(x.origin)} · expires ${relDay(x.expires)}</span></div>`).join("")
    || `<div class="none">none active</div>`;
  const v = s.vaults || {};
  const vault = `<div class="item">${esc(v.note || "")}</div>`;

  document.getElementById("commitments").innerHTML = `
    <div class="commit c-fu">
      <div class="k">Follow-ups <span class="n">${(s.followups || []).length}</span></div>${fu}
    </div>
    <div class="commit c-watch">
      <div class="k">Standing watches <span class="n">${(s.watches || []).length}</span></div>${w}
    </div>
    <div class="commit c-vault">
      <div class="k">Vaults <span class="n">${v.dm_vaults || 0}</span></div>${vault}
    </div>`;
}

/* ============ channel monitor: servers → channels → bot's-eye stream ============ */

let chTree = [];                 // [{key,name,color,icon,channels:[{id,label,turns,lastTs}]}]
let chServer = null, chChannel = null;

const SERVER_INITIALS = (name) => name.replace(/^#/, "").split(/[\s-]+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

/* a channel's full event stream: turns + skill invocations + episode reseeds */
function channelEvents(channelId, serverName) {
  const sameChan = (c) => c === channelId;
  const sameSrv = (sn) => serverName == null ? sn == null : sn === serverName;
  const ev = [];
  for (const t of monTrace) if (sameChan(t.channel) && sameSrv(t.server_name)) ev.push({ ts: t.ts, type: "turn", data: t });
  for (const s of monSkills) if (sameChan(s.channel) && sameSrv(s.server_name)) ev.push({ ts: s.ts, type: "skill", data: s });
  for (const e of monEpisodes) if (sameChan(e.channel) && sameSrv(e.server_name)) ev.push({ ts: e.ts, type: "episode", data: e });
  ev.sort((a, b) => new Date(a.ts) - new Date(b.ts));
  return ev;
}

function buildChannels() {
  const tree = [];
  for (const srv of (monStatus.servers || [])) {
    const channels = (srv.channels || []).map((c) => {
      const events = channelEvents(c, srv.name);
      const turns = events.filter((e) => e.type === "turn");
      return { id: c, label: c, events, turns, lastTs: events.length ? events[events.length - 1].ts : null };
    });
    tree.push({ key: srv.id, name: srv.name, color: srv.color || "#6b6353",
      icon: SERVER_INITIALS(srv.name), channels });
  }
  // Prime / DMs — DMs are channels of the mind-above-the-servers
  const dmTurns = monTrace.filter((t) => t.kind === "dm" || t.kind === "memory");
  const dmUsers = monStatus.dm_users || [...new Set(dmTurns.map((t) => t.channel.replace(/^DM · /, "")))];
  const dmChannels = dmUsers.map((u) => {
    const events = channelEvents(`DM · ${u}`, null);
    const turns = events.filter((e) => e.type === "turn");
    return { id: `DM · ${u}`, label: `@${u}`, dm: true, events, turns, lastTs: events.length ? events[events.length - 1].ts : null };
  });
  tree.push({ key: "dm", name: "Direct Messages", color: "#5a4f9e", icon: "DM", channels: dmChannels, prime: true });

  chTree = tree;
  if (!chServer) {
    let best = null;
    for (const s of tree) for (const c of s.channels) if (c.lastTs && (!best || new Date(c.lastTs) > new Date(best.ts))) best = { s: s.key, c: c.id, ts: c.lastTs };
    chServer = best ? best.s : tree[0].key;
    chChannel = best ? best.c : tree[0].channels[0]?.id;
  }
}

const AVATAR_PALETTE = ["#b5703a", "#2a6c8f", "#3a7d5c", "#9a5a8a", "#a8772a", "#4a4a78", "#5a8a4a", "#a84a4a"];
function avatarColor(name) {
  let h = 0; for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR_PALETTE[h % AVATAR_PALETTE.length];
}
const initials = (name) => name.replace(/^@/, "").slice(0, 2).toUpperCase();

function renderActivity() {
  const body = document.getElementById("activity-body");
  if (monView === "raw") { body.innerHTML = rawHTML(monMain); return; }
  renderChannelMonitor(body);   // episodes + skills now live inside the channel streams
}

function renderChannelMonitor(body) {
  const srv = chTree.find((s) => s.key === chServer) || chTree[0];
  const chan = srv.channels.find((c) => c.id === chChannel) || srv.channels[0];

  const rail = chTree.map((s) => `
    <div class="sicon ${s.key === srv.key ? "active" : ""}" data-server="${esc(s.key)}"
      style="background:${s.color};color:${s.color}"><span style="color:#fff">${esc(s.icon)}</span></div>`).join("");

  const list = srv.channels.map((c) => `
    <div class="citem ${c.id === chan?.id ? "active" : ""} ${c.turns.length ? "" : "quiet"}"
      data-channel="${esc(c.id)}" style="--accent:${srv.color}">
      ${c.dm ? "" : `<span class="hash">#</span>`}${esc(c.label.replace(/^#/, ""))}
      ${c.turns.length ? `<span class="badge-n">${c.turns.length}</span>` : ""}
    </div>`).join("");

  const stream = chan && chan.events.length
    ? streamHTML(chan, srv)
    : `<div class="feed"><div class="tsep"><span>the bot is in this channel · nothing logged yet</span></div></div>`;

  body.innerHTML = `
    <div class="chanmon">
      <div class="srail">${rail}</div>
      <div class="clist">
        <div class="chead">${esc(srv.name)}</div>
        ${list}
      </div>
      <div class="cstream">
        <div class="streamhead">
          <span class="chan">${chan?.dm ? "" : `<span class="hash">#</span>`}${esc((chan?.label || "").replace(/^#/, ""))}</span>
          <span class="sub">${chan ? chan.turns.length + " engagements · bot's-eye view" : ""}</span>
          <span class="where">${esc(srv.name)}</span>
        </div>
        ${stream}
      </div>
    </div>`;
  // open at the newest, like a real channel
  const feed = body.querySelector(".feed");
  if (feed) feed.scrollTop = feed.scrollHeight;
}

/* render one channel as a chronological stream of everything the bot did here */
function streamHTML(chan, srv) {
  let lastDay = "";
  const rows = chan.events.map((ev) => {
    const day = new Date(ev.ts).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
    let sep = "";
    if (day !== lastDay) { sep = `<div class="tsep"><span>${day}</span></div>`; lastDay = day; }
    const body = ev.type === "skill" ? skillStream(ev.data)
      : ev.type === "episode" ? episodeStream(ev.data)
      : turnStream(ev.data, chan);
    return sep + body;
  }).join("");
  return `<div class="feed">${rows}</div>`;
}

/* a skill invocation, grounded where it happened */
function skillStream(s) {
  return `
    <div class="actrow">
      <div class="av" style="background:var(--info)">⚡</div>
      <div>
        <div class="head"><span class="un" style="color:var(--info)">${esc(botId)}</span>
          <span class="skillchip">skill · ${esc(s.name)}</span>
          <span class="ts">${clockTime(new Date(s.ts))}</span></div>
        <div class="atrigger">${esc(s.trigger)}</div>
        <div class="atext">${esc(s.outcome)}</div>
        <div class="ameta">${s.container === "fresh" ? "fresh container" : "reused container"} · ${s.tokens.out} tokens out</div>
      </div>
    </div>`;
}

/* an episode = this channel's context consolidating into memory (a reseed) */
function episodeStream(e) {
  return `
    <div class="epmark ${e.reason}">
      <div class="eh">
        <span class="ebadge">◇ ${e.reason === "ceiling" ? "context reseeded · ceiling" : "context reseeded · idle"}</span>
        distilled to episode <b>${esc(e.title)}</b>
        <span class="emeta">${e.retained} msgs kept · was ${(e.tokens_before / 1000).toFixed(0)}k</span>
      </div>
      <div class="es">${esc(e.summary)}</div>
      <div class="epath">↳ memories/${esc(e.path)}</div>
    </div>`;
}

function chatMsg(user, text, ts, opts = {}) {
  const col = opts.bot ? "var(--ok)" : avatarColor(user);
  const av = opts.bot ? "AI" : initials(user);
  // raw mention tokens (<@123…>) render as a highlighted @botname
  const txt = opts.mention
    ? esc(text).replace(/&lt;@!?\d+&gt;/g, `<span class="at">@${esc(botId)}</span>`)
    : esc(text);
  const tags = opts.bot
    ? `<span class="bottag">BOT</span>${opts.ktag ? `<span class="ktag ${opts.ktag}">${KIND_LABEL[opts.ktag] || opts.ktag}</span>` : ""}`
    : "";
  return `
    <div class="msg ${opts.bot ? "bot k-" + (opts.ktag || "mention") : ""} ${opts.mention ? "mention" : ""}">
      <div class="av" style="background:${col}">${av}</div>
      <div>
        <div class="head"><span class="un">${opts.bot ? esc(botId) : esc(user)}</span>${tags}<span class="ts">${clockTime(ts)}</span></div>
        <div class="txt">${opts.mention ? txt : esc(text)}</div>
      </div>
    </div>`;
}

/* one engagement: incoming messages → bot x-ray → bot reply or silent */
function turnStream(t, chan) {
  const ts = new Date(t.ts);
  let html = "";

  // incoming messages (skip system "·" markers; render them as a thin note)
  for (const m of (t.triggers || [])) {
    if (m.user === "·") { html += `<div class="tsep"><span>${esc(m.text)}</span></div>`; continue; }
    html += chatMsg(m.user, m.text, ts, { mention: !!m.addressed });
  }

  // the X-RAY: the bot's private processing
  const tools = (t.tool_calls || []).map((c) => {
    const label = c.action || c.command || "", arg = c.id || c.path || c.query || c.name || "";
    return `<span class="tool"><b>${esc(c.tool)}</b>${label ? " · " + esc(label) : ""}${arg ? " " + esc(String(arg)) : ""}</span>`;
  }).join("");
  const nMsgs = (t.triggers || []).filter((m) => m.user !== "·").length;
  let verb;
  switch (t.kind) {
    case "mention":   verb = "read the @mention · replied"; break;
    case "dm":        verb = "read the DM · replied"; break;
    case "memory":    verb = "consent write · /memory"; break;
    case "proactive": verb = "agentic loop · opened unprompted"; break;
    case "followup":  verb = "follow-up fired"; break;
    case "relay":     verb = "reached across via the Prime"; break;
    case "watch":     verb = "standing watch resolved"; break;
    case "scan":      verb = `pulse · weighed ${nMsgs} message${nMsgs !== 1 ? "s" : ""} · replied`; break;
    case "silent":    verb = `pulse · weighed ${nMsgs} message${nMsgs !== 1 ? "s" : ""} · stayed quiet`; break;
    default:          verb = t.response ? "replied" : "no action";
  }
  const reasoning = t.thinking ? `<div class="reasoning">${esc(t.thinking)}</div>` : "";
  const provLine = t.provenance ? `<div class="prov">${esc(t.provenance)}</div>` : "";

  html += `
    <div class="xray ${t.response ? "" : "silent"}">
      <span class="xlabel ${t.thinking ? "has" : ""}"><span class="verb">${verb}</span></span>
      ${reasoning}
      ${tools ? `<div class="tools">${tools}</div>` : ""}
      ${provLine}
      ${t.response ? "" : `<div class="outcome">${t.decision ? esc(t.decision) + "." : "Nothing here needed a reply."}</div>`}
    </div>`;

  // the bot's actual message in the channel (if it spoke)
  if (t.response) html += chatMsg(botId, t.response, ts, { bot: true, ktag: t.kind === "mention" || t.kind === "scan" ? null : t.kind });

  return html;
}

/* Episodes & reseeds — the context-consolidation timeline */
function episodesHTML(eps) {
  return `<div class="evts">${eps.map((e) => {
    const where = e.server_name ? `${esc(e.channel)} · ${esc(e.server_name)}` : esc(e.channel);
    return `
    <div class="evt">
      <div class="when">
        <div class="time figs">${dayClock(e.ts)}</div>
        <div class="where">${where}</div>
      </div>
      <div class="what">
        <div class="evt-head">
          <span class="badge ${e.reason}">${e.reason === "ceiling" ? "ceiling reseed" : "idle reseed"}</span>
          <b>${esc(e.title)}</b>
          <span class="evt-meta figs">${e.retained} msgs retained · ${(e.tokens_before / 1000).toFixed(0)}k before</span>
        </div>
        <div class="evt-body">${esc(e.summary)}</div>
        <div class="evt-path">${esc(e.path)}</div>
      </div>
    </div>`;
  }).join("")}</div>`;
}

/* Skills & capabilities the bot invoked */
function skillsHTML(rows) {
  return `<div class="evts">${rows.map((s) => {
    const where = s.server_name ? `${esc(s.channel)} · ${esc(s.server_name)}` : esc(s.channel);
    return `
    <div class="evt">
      <div class="when">
        <div class="time figs">${dayClock(s.ts)}</div>
        <div class="where">${where}</div>
      </div>
      <div class="what">
        <div class="evt-head">
          <span class="badge skill">${esc(s.name)}</span>
          <span class="evt-meta">${s.container === "fresh" ? "fresh container" : "reused container"} · ${s.tokens.out} out</span>
        </div>
        <div class="evt-trigger">${esc(s.trigger)}</div>
        <div class="evt-body">${esc(s.outcome)}</div>
      </div>
    </div>`;
  }).join("")}</div>`;
}

function renderGauges(s) {
  const ctx = s.context, pct = Math.round((ctx.tokens / ctx.ceiling) * 100);
  const warn = pct >= 85;
  const t = s.tokens_today;
  const ctxCls = pct >= 90 ? "hot" : warn ? "warn" : "";
  document.getElementById("gauges").innerHTML = `
    <div class="gauge g-ok">
      <div class="k">Reactive engine</div>
      <div class="v"><span class="dot green"></span>${s.reactive.state}</div>
      <div class="meta">last reply ${agoInWords(s.reactive.last_response)} · ${esc(s.reactive.last_channel)}</div>
    </div>
    <div class="gauge g-prime">
      <div class="k">Agentic engine</div>
      <div class="v"><span class="dot ${s.agentic.state === "sleeping" ? "faint" : "amber"}"></span>${s.agentic.state}</div>
      <div class="meta">next check ${s.agentic.next_check ? clockTime(new Date(s.agentic.next_check)) : "—"} · ${s.agentic.followups_pending} follow-ups pending</div>
    </div>
    <div class="gauge g-ctx ${ctxCls}">
      <div class="k">Live context · ${esc(ctx.channel)}</div>
      <div class="v figs">${ctx.tokens.toLocaleString()}<small> / ${ctx.ceiling.toLocaleString()}</small></div>
      <div class="bar ${ctxCls || ""}"><i style="width:${pct}%"></i></div>
      <div class="meta">${ctx.live_messages} messages live · ${ctx.reseeds_today} reseeds today</div>
    </div>
    <div class="gauge g-info">
      <div class="k">Tokens today</div>
      <div class="v figs">${tokens(t.cache_read + t.uncached_in + t.out)}${
        s.cost_today_usd != null ? `<small> ≈ $${s.cost_today_usd.toFixed(2)}</small>` : ""}</div>
      <div class="meta figs">${tokens(t.cache_read)} read · ${tokens(t.uncached_in)} in · ${tokens(t.out)} out</div>
    </div>`;
}

function renderStatband(st) {
  const max = Math.max(...st.per_day.map((d) => d.msgs));
  const bars = st.per_day.map((d, i) => {
    const h = Math.round((d.msgs / max) * 100);
    const last = i === st.per_day.length - 1;
    return `<i class="${last ? "today" : ""}" style="height:${h}%" title="${d.d}: ${d.msgs}"></i>`;
  }).join("");
  const day = (d) => d.slice(5).replace("-", "/");
  document.getElementById("statband").innerHTML = `
    <div class="statline figs">
      <div class="s"><div class="n">${st.messages_stored.toLocaleString()}</div><div class="l">Messages stored</div></div>
      <div class="s"><div class="n">${st.episodes}</div><div class="l">Episodes</div></div>
      <div class="s"><div class="n">${st.attachments}</div><div class="l">Attachments</div></div>
      <div class="s"><div class="n">${st.repository_files}</div><div class="l">Repo files</div></div>
      <div class="s"><div class="n">${st.memory_files}</div><div class="l">Memory files</div></div>
    </div>
    <div class="spark">
      <div class="cap">Messages · 7 days</div>
      <div class="bars">${bars}</div>
      <div class="axis"><span>${day(st.per_day[0].d)}</span><span>today</span></div>
    </div>`;
}

const KIND_LABEL = {
  mention: "@ mention", scan: "periodic scan", silent: "scanned · silent",
  proactive: "proactive", followup: "follow-up", dm: "direct · prime",
  memory: "/memory", relay: "ask prime", watch: "watch resolved",
};
const PRIME_KINDS = new Set(["dm", "memory", "relay", "watch"]);

function turnHTML(t) {
  const tok = t.tokens;
  const trigs = t.triggers || [];

  // trigger block — summary for ambient kinds, then messages (extras hidden)
  let trig = "";
  if (t.kind === "scan" || t.kind === "silent")
    trig += `<div class="trg-sum">Periodic scan · ${t.scan_count || trigs.length} new messages · not addressed</div>`;
  else if (t.kind === "proactive")
    trig += `<div class="trg-sum">Agentic loop · no inbound trigger</div>`;

  const ambient = t.kind === "scan" || t.kind === "silent";
  let shown = 0, hidden = 0;
  trig += trigs.map((m) => {
    if (m.user === "·") return `<div class="trg meta">${esc(m.text)}</div>`;
    const isExtra = ambient && shown >= 1;
    if (isExtra) hidden++; else shown++;
    return `<div class="trg ${m.addressed ? "addr" : ""} ${isExtra ? "extra" : ""}"><span class="by">${esc(m.user)}</span>${esc(m.text)}</div>`;
  }).join("");
  if (hidden) trig += `<div class="trg-more">+${hidden} more message${hidden > 1 ? "s" : ""}</div>`;

  // outcome — the anchor
  let out;
  if (t.response) out = `<div class="rep"><span class="rl">${PRIME_KINDS.has(t.kind) ? "prime" : "reply"}</span>${esc(t.response)}</div>`;
  else if (t.decision) out = `<div class="rep silent"><span class="rl">no action</span>${esc(t.decision)}.</div>`;
  else out = `<div class="rep silent"><span class="rl">silent</span>stayed out — nothing worth interjecting.</div>`;

  const prov = t.provenance ? `<div class="prov">${esc(t.provenance)}</div>` : "";

  // details — reasoning + tools, collapsed
  const tools = (t.tool_calls || []).map((c) => {
    const label = c.action || c.command || "";
    const arg = c.id || c.path || c.query || c.name || "";
    return `<span class="tool"><b>${esc(c.tool)}</b>${label ? " · " + esc(label) : ""}${arg ? " " + esc(String(arg)) : ""}</span>`;
  }).join("");
  const nTools = (t.tool_calls || []).length;
  const bits = [];
  if (t.thinking) bits.push(`<div class="reasoning"><span class="label">Reasoning</span>${esc(t.thinking)}</div>`);
  if (tools) bits.push(`<div class="tools">${tools}</div>`);
  const tgLabel = [t.thinking ? "reasoning" : null, nTools ? `${nTools} tool${nTools > 1 ? "s" : ""}` : null].filter(Boolean).join(" · ");
  const details = bits.length ? `<div class="det-tg">${tgLabel}</div><div class="details">${bits.join("")}</div>` : "";

  const loc = t.server_name ? `${esc(t.channel)} · ${esc(t.server_name)}` : esc(t.channel);
  return `
    <div class="turn k-${t.kind}">
      <div class="thead">
        <span class="kind">${KIND_LABEL[t.kind] || t.kind}</span>
        <span class="loc">${loc}</span>
        <span class="time">${clockTime(new Date(t.ts))}</span>
        <span class="toks figs">${tok.uncached_in}·${tokens(tok.cache_read)}·${tok.out}</span>
      </div>
      <div class="exch">${trig}${out}${prov}</div>
      ${details}
    </div>`;
}

function rawHTML(lines) {
  return `<div class="rawlog">${lines.map((l) => `
    <div class="line">
      <span class="t figs">${clockTime(new Date(l.ts)).replace(/ [ap]\.m\./, "")}</span>
      <span class="lv ${l.level}">${l.level}</span>
      <span class="m"><span class="src">[${esc(l.src)}]</span> ${esc(l.msg)}</span>
    </div>`).join("")}</div>`;
}

let activityWired = false;
function wireActivity() {
  if (activityWired) return; activityWired = true;
  document.getElementById("activity-body").addEventListener("click", (e) => {
    // server rail
    const sIcon = e.target.closest(".sicon");
    if (sIcon) {
      chServer = sIcon.dataset.server;
      const srv = chTree.find((s) => s.key === chServer);
      const withActivity = srv.channels.find((c) => c.turns.length) || srv.channels[0];
      chChannel = withActivity?.id;
      renderActivity();
      return;
    }
    // channel list
    const cItem = e.target.closest(".citem");
    if (cItem) { chChannel = cItem.dataset.channel; renderActivity(); return; }
    // x-ray reasoning expander
    const xl = e.target.closest(".xlabel.has");
    if (xl) { xl.closest(".xray").classList.toggle("open"); return; }
  });
  document.querySelectorAll(".subtoggle a").forEach((a) =>
    a.addEventListener("click", () => {
      monView = a.dataset.view;
      document.querySelectorAll(".subtoggle a").forEach((x) => x.classList.toggle("active", x === a));
      renderActivity();
    }));
}

/* ============================ Configure ============================ */

/* Knowledge mirrored from core/config.py + internal_constants.py. The form
   renders ESSENTIALS first (always, with defaults filled in even when the
   YAML omits a key), then everything else inside a collapsed Advanced
   section. Checkboxes mean one thing: checked = on. */

const MODEL_OPTIONS = [
  "claude-fable-5", "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5",
];
// mirrors internal_constants._EFFORT_CAPABLE_MARKERS
const EFFORT_MARKERS = ["fable", "opus-4-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6"];
const supportsEffort = (m) => EFFORT_MARKERS.some((x) => (m || "").includes(x));

// Curated IANA timezones for the dropdown (no more typing); the current value
// is preserved even if it's not in this list.
const TIMEZONES = [
  "UTC",
  "America/Los_Angeles", "America/Denver", "America/Phoenix", "America/Chicago",
  "America/New_York", "America/Toronto", "America/Sao_Paulo",
  "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Madrid",
  "Europe/Rome", "Europe/Athens", "Europe/Moscow",
  "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos",
  "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai",
  "Asia/Singapore", "Asia/Tokyo", "Asia/Seoul",
  "Australia/Sydney", "Pacific/Auckland",
];

// Backfill slider stops. "off" disables backfill; "0" means unlimited (the
// framework's backfill_days: 0). The slider maps to backfill_enabled + days.
const BACKFILL_STOPS = [30, 90, 180];
const nearestBackfillStop = (d) =>
  BACKFILL_STOPS.reduce((a, b) => (Math.abs(b - d) < Math.abs(a - d) ? b : a));

/* config.py defaults - merged under the loaded YAML so every essential
   renders even in a minimal config */
const DEFAULTS = {
  discord: { servers: [], timezone: "UTC", status: "Powered by Claude",
    allow_bot_interactions: false, backfill_enabled: true, backfill_days: 30 },
  personality: { base_prompt: "", reaction_usage: "moderate" },
  reactive: { enabled: true, always_respond_to_mentions: true,
    rate_limit: "moderate", check_interval_seconds: 60 },
  agentic: { enabled: false, check_interval_hours: 1.0,
    followups: { enabled: false },
    consolidation: { enabled: true, interval_days: 7 },
    proactive: { enabled: false, intensity: "moderate",
      quiet_hours: [0, 1, 2, 3, 4, 5, 6], allowed_channels: [] } },
  api: { model: "claude-sonnet-4-6", max_tokens: 4096, context_messages: 30,
    context_tokens: 80000, effort: null, consolidation_model: "claude-sonnet-4-6",
    thinking: { enabled: true }, web_search: { enabled: false } },
  mcp: { enabled: false },
  skills: { include_anthropic_skills: true, default_skills: [] },
  attachments: { enabled: false, backfill_enabled: true, backfill_days: 30,
    repository: { enabled: true } },
  logging: { level: "INFO" },
  vaults: [],
};

// description: a note-to-self the bot never sees — dropped from the UI.
// discord.backfill_enabled: owned by the backfill slider, not shown on its own.
// api.max_tokens: derived from context_tokens + model, never user-set.
const HIDDEN = new Set([
  "bot_id", "discord.token_env_var", "logging.file",
  "description", "discord.backfill_enabled", "api.max_tokens",
  "attachments.backfill_enabled", "attachments.backfill_days",
]);
// Whole sections that belong to the Integrations tab, not Configure.
const HIDDEN_SECTIONS = new Set(["mcp", "skills"]);

const HINTS = {
  "name": { label: "Name", help: "Display name in the dashboard." },
  "description": { label: "Description", help: "A note to yourself - the bot doesn't see it." },
  "discord.servers": { label: "Servers", help: "Which of its Discord servers this bot engages with. Inviting the bot to a server happens on Discord; this only selects among them.", widget: "guilds" },
  "discord.status": { label: "Status", help: "Activity line shown under the bot in Discord." },
  "discord.timezone": { label: "Timezone", help: "Drives quiet-hours and timestamps.", options: TIMEZONES },
  "discord.allow_bot_interactions": { label: "Reply to other bots", help: "Whether other bots can trigger this one." },
  "discord.backfill_enabled": { label: "Backfill history", help: "Pull prior channel history on first join." },
  "discord.backfill_days": { label: "Backfill history", help: "How much prior channel history to pull on first join. Off skips it entirely.", widget: "backfill" },

  "personality.base_prompt": { label: "Personality", help: "The standing prompt — who this bot is. The single most load-bearing field.", widget: "prompt" },
  "personality.reaction_usage": { label: "Reaction usage", help: "How often it adds emoji reactions.", choices: [
    ["never", "Never", "No emoji reactions at all."],
    ["rare", "Rare", "An occasional reaction, only when it really lands."],
    ["moderate", "Moderate", "Reacts when it fits the moment."],
    ["frequent", "Frequent", "Leans on reactions as part of how it talks."]] },

  "reactive.enabled": { label: "Reactive engine", help: "Answers @mentions and scans conversation." },
  "reactive.always_respond_to_mentions": { label: "Always answer @mentions", help: "Guarantees a reply when pinged." },
  "reactive.rate_limit": { label: "Rate limit", help: "Preset governing how often it answers.", choices: [
    ["strict", "Strict", "Answers sparingly; long cooldowns between replies."],
    ["moderate", "Moderate", "A balanced replying cadence."],
    ["permissive", "Permissive", "Replies readily; short cooldowns."],
    ["unlimited", "Unlimited", "No rate limit — answers every time it decides to."]] },
  "reactive.check_interval_seconds": { label: "Scan interval", help: "Seconds between periodic conversation scans.", min: 1 },

  "agentic.enabled": { label: "Agentic engine", help: "The background loop — proactive engagement and follow-ups." },
  "agentic.check_interval_hours": { label: "Loop interval", help: "Hours between agentic wake-ups." },
  "agentic.followups.enabled": { label: "Follow-ups", help: "Lets the bot schedule and fire follow-ups." },
  "agentic.proactive.enabled": { label: "Proactive engagement", help: "Opening conversations unprompted." },
  "agentic.proactive.intensity": { label: "Intensity", help: "How eagerly it opens conversation when a channel goes quiet.", choices: [
    ["gentle", "Gentle", "Rarely opens up — long idle window, low daily cap."],
    ["moderate", "Moderate", "Occasional openers when a channel goes quiet."],
    ["active", "Active", "Reaches out often — short idle window, higher cap."]] },
  "agentic.proactive.quiet_hours": { label: "Quiet hours", help: "Local-clock hours when it stays silent. Click hours to toggle.", widget: "hours" },
  "agentic.proactive.allowed_channels": { label: "Proactive channels", help: "Where unprompted openings are permitted. Empty = none.", widget: "channels" },

  "api.model": { label: "Model", options: MODEL_OPTIONS, help: "The mind. Fable 5 is the most capable; Haiku the most economical." },
  "api.max_tokens": { label: "Max tokens", help: "Cap on tokens per response." },
  "api.context_messages": { label: "Context messages", help: "Live messages kept before reseed (5–100).", min: 5, max: 100 },
  "api.context_tokens": { label: "Context ceiling", help: "Token budget before distill + reseed." },
  "api.effort": { label: "Effort", help: "Depth-of-thought dial. Only models with effort support show this.", widget: "effort" },
  "api.consolidation_model": { label: "Memory model", options: ["claude-sonnet-4-6", "claude-fable-5", "claude-opus-4-8"], help: "Distills episodes, runs weekly reconsolidation, and powers induction. Uses medium thinking effort." },
  "agentic.consolidation.enabled": { label: "Reconsolidation", help: "The background pass that revisits and tidies the bot's own memory. Costs tokens (Batches API, half-price) — turn it off to freeze memory at what the bot writes live." },
  "agentic.consolidation.interval_days": { label: "Reconsolidation interval", help: "Days between reconsolidation passes per server. Longer = cheaper and more stable, less fresh.", min: 1 },
  "api.thinking.enabled": { label: "Extended thinking", help: "Adaptive thinking; the model decides when." },
  "api.web_search.enabled": { label: "Web search", help: "Live web lookups when conversation calls for it." },

  "mcp.enabled": { label: "MCP servers", help: "Load Model Context Protocol servers (see Integrations)." },
  "skills.include_anthropic_skills": { label: "Anthropic skills", help: "Make the built-in pdf / xlsx / docx / pptx skills available for the bot to use on demand." },
  "skills.default_skills": { label: "Preloaded skills", help: "Skills loaded at the start of every conversation; anything else is requested only when needed.", widget: "chips" },

  "attachments.enabled": { label: "Attachments", help: "Unified file/image handling." },
  "attachments.backfill_enabled": { label: "Backfill attachments", help: "Pull prior attachments on first join." },
  "attachments.backfill_days": { label: "Attachment backfill days", help: "0 = unlimited." },
  "attachments.repository.enabled": { label: "File repository", help: "Per-server drive the bot manages." },

  "logging.level": { label: "Log level", help: "How much detail the bot writes to its log file.", choices: [
    ["DEBUG", "Debug", "Everything, including internals. Noisy."],
    ["INFO", "Info", "Normal operational detail. The default."],
    ["WARNING", "Warning", "Only warnings and errors."],
    ["ERROR", "Error", "Errors only."]] },

  "vaults": { label: "Vaults", help: "Channel/server ids whose content never leaves them. Empty = none.", widget: "chips" },
};

// Horizontal config sub-tabs. The named tabs claim their fields; an "Advanced"
// tab is computed from everything left over. "__token__" is the Discord token
// control (write-only, lives in .env).
const CONFIG_TABS = [
  { title: "Identity", items: [
    "name", "discord.status", "personality.base_prompt", "personality.reaction_usage"] },
  { title: "Connection", items: [
    "__token__", "discord.servers", "discord.timezone",
    "discord.backfill_days", "discord.allow_bot_interactions"] },
  { title: "Brain", items: [
    "api.model", "api.effort", "api.thinking.enabled",
    "api.web_search.enabled", "api.consolidation_model",
    "agentic.consolidation.enabled", "agentic.consolidation.interval_days"] },
  { title: "Engagement", items: [
    "reactive.enabled", "reactive.rate_limit", "agentic.enabled",
    "agentic.proactive.enabled", "agentic.proactive.intensity",
    "agentic.proactive.allowed_channels", "agentic.proactive.quiet_hours",
    "agentic.followups.enabled"] },
];

/* Named tabs + an Advanced tab. Advanced holds every leaf not claimed above
   and not in a hidden section (mcp/skills live in Integrations), grouped by
   section so it isn't one undifferentiated scroll. */
function configTabs() {
  const claimed = new Set(
    CONFIG_TABS.flatMap((g) => g.items).filter((p) => p !== "__token__"));
  const groups = [];
  for (const section of Object.keys(cfg)) {
    if (HIDDEN_SECTIONS.has(section)) continue;
    const items = collectPaths(cfg[section], section)
      .filter((p) => !claimed.has(p) && !HIDDEN.has(p));
    if (items.length) groups.push({ section, items });
  }
  return [...CONFIG_TABS, { title: "Advanced", groups }];
}

let cfg = null, cfgDirty = false, setupInfo = null;

function deepMerge(base, over) {
  if (Array.isArray(base) || Array.isArray(over) || typeof base !== "object"
      || typeof over !== "object" || base === null || over === null)
    return over === undefined ? base : over;
  const out = { ...base };
  for (const k of Object.keys(over)) out[k] = deepMerge(base[k], over[k]);
  return out;
}

const getNested = (obj, path) =>
  path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

function setNested(obj, path, value) {
  const keys = path.split(".");
  let cur = obj;
  for (const k of keys.slice(0, -1)) cur = (cur[k] = cur[k] ?? {});
  cur[keys.at(-1)] = value;
}

async function loadConfigure() {
  [cfg, setupInfo] = await Promise.all([
    apiGet(A("/config")), apiGet("/api/setup").catch(() => null)]);
  cfg = deepMerge(DEFAULTS, cfg);
  const form = document.getElementById("cfg-form");
  buildForm();
  form.addEventListener("input", () => setDirty(true));
  form.addEventListener("change", (e) => {
    // model switch shows/hides the effort dial
    if (e.target.closest('.field[data-path="api.model"]')) renderEffortGate();
  });
  form.addEventListener("click", onFormClick);
  form.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches(".chip-in")) {
      e.preventDefault(); addChipFrom(e.target);
    }
  });
  document.getElementById("cfg-save").addEventListener("click", saveConfig);
  document.getElementById("cfg-reset").addEventListener("click", () => {
    buildForm();
    document.getElementById("cfg-banner").className = "cfg-banner";
    setDirty(false);
  });
}

function buildForm() {
  const tabs = configTabs();
  const nav = tabs.map((t, i) =>
    `<a class="cfgtab ${i === 0 ? "active" : ""}" data-cfgtab="${i}">${esc(t.title)}</a>`).join("");
  const panels = tabs.map((t, i) => {
    const body = t.groups
      ? t.groups.map((g) =>
          `<h4 class="cfg-section">${esc(g.section)}</h4>${g.items.map(fieldHTML).join("")}`).join("")
      : t.items.map((p) => p === "__token__" ? tokenFieldHTML() : fieldHTML(p)).join("");
    return `<div class="cfgpanel ${i === 0 ? "active" : ""}" data-cfgpanel="${i}">${body}</div>`;
  }).join("");
  document.getElementById("cfg-form").innerHTML =
    `<nav class="cfgtabs">${nav}</nav><div class="cfgpanels">${panels}</div>`;
  renderEffortGate();
  refreshTokenStatus();
}

/* every leaf path under a config subtree */
function collectPaths(val, path) {
  if (Array.isArray(val) || typeof val !== "object" || val === null) return [path];
  return Object.keys(val).flatMap((k) => collectPaths(val[k], `${path}.${k}`));
}

function fieldHTML(path) {
  if (HIDDEN.has(path)) return "";
  const val = getNested(cfg, path);
  const h = HINTS[path] || {};
  const label = h.label || path.split(".").pop().replace(/_/g, " ");
  const widget = h.widget || inferWidget(val, h);
  const lab = `<div class="lab">
      <div class="name">${esc(label)}</div>
      <div class="path">${esc(path)}</div>
      ${h.help ? `<div class="help">${esc(h.help)}</div>` : ""}
    </div>`;
  return `<div class="field" data-path="${esc(path)}" data-widget="${esc(widget)}">
    ${lab}<div class="ctl">${ctlHTML(widget, val, h, path)}</div></div>`;
}

function inferWidget(val, h) {
  if (h.choices) return "radioset";
  if (h.options) return "select";
  if (typeof val === "boolean") return "toggle";
  if (typeof val === "number") return "number";
  if (Array.isArray(val)) return "chips";
  if (typeof val === "string" && val.length > 80) return "prompt";
  return "text";
}

function chipsHTML(items, editable = true) {
  const chips = (items || []).map((x) =>
    `<span class="chip" data-val="${esc(String(x))}">${esc(String(x))}${
      editable ? `<button class="x" type="button" title="remove">×</button>` : ""}</span>`).join("");
  const adder = editable
    ? `<span class="chip-adder"><input class="chip-in" placeholder="add…">` +
      `<button class="chip-add" type="button">+</button></span>` : "";
  return `<div class="chips ${editable ? "edit" : ""}">${chips}${adder}</div>`;
}

function hoursHTML(selected) {
  const sel = new Set((selected || []).map(Number));
  const cells = Array.from({ length: 24 }, (_, h) =>
    `<span class="hr ${sel.has(h) ? "sel" : ""}" data-h="${h}">${h}</span>`).join("");
  return `<div class="hours">${cells}</div>
    <div class="hours-cap">shaded = quiet (host's local clock)</div>`;
}

function ctlHTML(widget, val, h, path) {
  switch (widget) {
    case "toggle":
      return `<label class="toggle"><input type="checkbox" ${val ? "checked" : ""}></label>`;
    case "radioset": {
      // selectable cards: each option carries a one-line tagline (item 5/6)
      const name = `rs-${(path || "x").replace(/\W/g, "_")}`;
      return `<div class="radioset">${h.choices.map(([v, lbl, desc]) =>
        `<label class="ropt">
           <input type="radio" name="${name}" value="${esc(v)}" ${v === val ? "checked" : ""}>
           <span class="rlab">${esc(lbl)}</span>
           <span class="rdesc">${esc(desc)}</span>
         </label>`).join("")}</div>`;
    }
    case "prompt":
      return `<textarea class="prompt">${esc(String(val ?? ""))}</textarea>`;
    case "number":
      return `<input type="number" value="${esc(String(val))}" ${h.min != null ? `min="${h.min}"` : ""} ${h.max != null ? `max="${h.max}"` : ""}><div class="err"></div>`;
    case "chips":
      return chipsHTML(val);
    case "hours":
      return hoursHTML(val);
    case "guilds":
      return chipsHTML(val) +
        `<button class="btn small fetch-guilds" type="button">Choose from Discord…</button>
         <div class="picker"></div><div class="err"></div>`;
    case "channels":
      return chipsHTML(val) +
        `<button class="btn small fetch-channels" type="button">Choose from Discord…</button>
         <div class="picker"></div><div class="err"></div>`;
    case "effort": {
      const cur = val ?? "";
      const titleize = (o) => o.charAt(0).toUpperCase() + o.slice(1);
      const opts = ["", "low", "medium", "high", "max"].map((o) =>
        `<option value="${o}" ${o === cur ? "selected" : ""}>${o ? titleize(o) : "Default (high)"}</option>`).join("");
      return `<select>${opts}</select>`;
    }
    case "backfill": {
      const enabled = getNested(cfg, "discord.backfill_enabled");
      const days = Number(val) || 0;
      const cur = !enabled ? "off" : (days === 0 ? "0" : String(nearestBackfillStop(days)));
      const stops = [["off", "Off"], ["30", "30 days"], ["90", "90 days"],
                     ["180", "180 days"], ["0", "Unlimited"]];
      return `<div class="scale" data-sel="${cur}">${stops.map(([v, lab]) =>
        `<button type="button" class="stop ${v === cur ? "on" : ""}" data-v="${v}">${lab}</button>`).join("")}</div>
        <div class="scale-cap">left disables backfill; right pulls all history</div>`;
    }
    case "select": {
      const opts = h.options || [];
      const list = (val == null || opts.includes(val)) ? opts : [val, ...opts];
      return `<select>${list.map((o) =>
        `<option ${o === val ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
    }
    default:
      return `<input type="text" value="${esc(String(val ?? ""))}"><div class="err"></div>`;
  }
}

/* --- Discord token (write-only; lives in .env, not the YAML) --- */

function tokenFieldHTML() {
  return `<div class="field" data-token>
    <div class="lab">
      <div class="name">Discord bot token</div>
      <div class="path">stored locally in .env</div>
      <div class="help">From the Discord Developer Portal → your app → Bot →
        Reset Token. Pasted once, validated against Discord, never shown again.</div>
    </div>
    <div class="ctl tokenctl">
      <div class="tok-status">…</div>
      <div class="tok-row">
        <input type="password" class="tok-in" placeholder="paste bot token" autocomplete="off">
        <button class="btn small tok-save" type="button">Validate &amp; save</button>
      </div>
      <div class="err"></div>
    </div></div>`;
}

function refreshTokenStatus() {
  const el = document.querySelector(".tok-status");
  if (!el) return;
  const me = (setupInfo?.bots || []).find((b) => b.bot_id === botId);
  el.innerHTML = me?.token_set
    ? `<span class="chip on"><span class="led"></span>token set</span>`
    : `<span class="chip off"><span class="led"></span>no token yet — the bot can't log in</span>`;
}

async function saveToken(field) {
  const input = field.querySelector(".tok-in");
  const btn = field.querySelector(".tok-save");
  const err = field.querySelector(".err");
  err.style.display = "none";
  if (!input.value.trim()) return;
  btn.disabled = true; btn.textContent = "Checking…";
  try {
    const r = await fetch(A("/token"), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: input.value.trim() }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `Discord validation failed (${r.status})`);
    input.value = "";
    field.querySelector(".tok-status").innerHTML =
      `<span class="chip on"><span class="led"></span>connected as <b>${esc(body.bot_user)}</b>${
        body.restart_required ? " — restart to apply" : ""}</span>`;
    setupInfo = await apiGet("/api/setup").catch(() => setupInfo);
  } catch (e) {
    err.textContent = e.message; err.style.display = "block";
  }
  btn.disabled = false; btn.textContent = "Validate & save";
}

/* --- guild / channel pickers (read live from Discord via the daemon) --- */

async function openGuildPicker(field) {
  const picker = field.querySelector(".picker");
  const err = field.querySelector(".err");
  err.style.display = "none";
  picker.innerHTML = `<div class="empty">asking Discord…</div>`;
  try {
    const guilds = await apiGet(A("/guilds"));
    const current = new Set(chipValues(field));
    picker.innerHTML = guilds.map((g) => `
      <label class="pick"><input type="checkbox" value="${esc(g.id)}"
        ${current.has(g.id) ? "checked" : ""}>
        ${esc(g.name)} <span class="pid">${esc(g.id)}</span></label>`).join("")
      || `<div class="empty">this bot account isn't in any server yet — invite it from the Discord Developer Portal (OAuth2 → URL generator)</div>`;
    field.querySelector(".chips")?.classList.add("superseded");
  } catch (e) {
    picker.innerHTML = "";
    err.textContent = /409/.test(e.message)
      ? "set the Discord token first, then I can list its servers"
      : `couldn't reach Discord: ${e.message}`;
    err.style.display = "block";
  }
  setDirty(true);
}

async function openChannelPicker(field) {
  const picker = field.querySelector(".picker");
  const err = field.querySelector(".err");
  err.style.display = "none";
  // channels come from the servers currently selected in THIS form
  const serversField = document.querySelector('.field[data-path="discord.servers"]');
  const serverIds = serversField ? fieldValue(serversField) : [];
  if (!serverIds.length) {
    err.textContent = "pick the bot's servers first (Connection, above)";
    err.style.display = "block";
    return;
  }
  picker.innerHTML = `<div class="empty">asking Discord…</div>`;
  try {
    const current = new Set(chipValues(field));
    const parts = [];
    for (const sid of serverIds) {
      const chans = await apiGet(A(`/guilds/${encodeURIComponent(sid)}/channels`));
      parts.push(chans.map((c) => `
        <label class="pick"><input type="checkbox" value="${esc(c.id)}"
          ${current.has(c.id) ? "checked" : ""}>
          <span class="hash">${c.type === 2 ? "🔊" : "#"}</span>${esc(c.name)} <span class="pid">${esc(c.id)}</span></label>`).join(""));
    }
    picker.innerHTML = parts.join("") || `<div class="empty">no text channels found</div>`;
    field.querySelector(".chips")?.classList.add("superseded");
  } catch (e) {
    picker.innerHTML = "";
    err.textContent = /409/.test(e.message)
      ? "set the Discord token first, then I can list channels"
      : `couldn't reach Discord: ${e.message}`;
    err.style.display = "block";
  }
  setDirty(true);
}

/* --- shared form interaction --- */

function onFormClick(e) {
  if (e.target.matches(".cfgtab")) {
    const i = e.target.dataset.cfgtab;
    document.querySelectorAll(".cfgtab").forEach((t) =>
      t.classList.toggle("active", t.dataset.cfgtab === i));
    document.querySelectorAll(".cfgpanel").forEach((p) =>
      p.classList.toggle("active", p.dataset.cfgpanel === i));
    return;
  }
  const field = e.target.closest(".field");
  if (e.target.matches(".scale .stop")) {
    const scale = e.target.closest(".scale");
    scale.querySelectorAll(".stop").forEach((s) => s.classList.toggle("on", s === e.target));
    scale.dataset.sel = e.target.dataset.v;
    setDirty(true); return;
  }
  if (e.target.matches(".hr")) {
    e.target.classList.toggle("sel"); setDirty(true); return;
  }
  if (e.target.matches(".chip .x")) {
    e.target.closest(".chip").remove(); setDirty(true); return;
  }
  if (e.target.matches(".chip-add")) {
    addChipFrom(e.target.parentElement.querySelector(".chip-in")); return;
  }
  if (e.target.matches(".fetch-guilds")) { openGuildPicker(field); return; }
  if (e.target.matches(".fetch-channels")) { openChannelPicker(field); return; }
  if (e.target.matches(".tok-save")) { saveToken(field); return; }
}

function addChipFrom(input) {
  const v = (input.value || "").trim();
  if (!v) return;
  input.value = "";
  input.closest(".chips").querySelector(".chip-adder").insertAdjacentHTML(
    "beforebegin",
    `<span class="chip" data-val="${esc(v)}">${esc(v)}<button class="x" type="button">×</button></span>`);
  setDirty(true);
}

const chipValues = (field) =>
  [...field.querySelectorAll(".chip[data-val]")].map((c) => c.dataset.val);

function setDirty(d) {
  cfgDirty = d;
  document.getElementById("cfg-dirty").textContent = d ? "unsaved changes" : "";
  document.getElementById("cfg-save").disabled = !d;
}

/* Effort only renders meaningfully on effort-capable models; switching to a
   model without it hides the dial (and the collected value goes null). */
function renderEffortGate() {
  const modelSel = document.querySelector('.field[data-path="api.model"] select');
  const effortField = document.querySelector('.field[data-path="api.effort"]');
  if (!modelSel || !effortField) return;
  effortField.style.display = supportsEffort(modelSel.value) ? "" : "none";
}

/* read one field's current value out of the DOM */
function fieldValue(field) {
  const widget = field.dataset.widget;
  const ctl = field.querySelector(".ctl");
  switch (widget) {
    case "toggle": return ctl.querySelector("input[type=checkbox]").checked;
    case "backfill": {
      const on = ctl.querySelector(".scale .stop.on");
      return on ? on.dataset.v : "off";
    }
    case "prompt": return ctl.querySelector("textarea.prompt").value;
    case "number": return Number(ctl.querySelector("input[type=number]").value);
    case "hours":
      return [...ctl.querySelectorAll(".hr.sel")].map((c) => Number(c.dataset.h)).sort((a, b) => a - b);
    case "effort": {
      const modelSel = document.querySelector('.field[data-path="api.model"] select');
      if (modelSel && !supportsEffort(modelSel.value)) return null;
      return ctl.querySelector("select").value || null;
    }
    case "select": return ctl.querySelector("select").value;
    case "radioset":
      return ctl.querySelector("input[type=radio]:checked")?.value ?? null;
    case "guilds": case "channels": {
      const checks = [...ctl.querySelectorAll(".picker input[type=checkbox]")];
      if (checks.length) return checks.filter((c) => c.checked).map((c) => c.value);
      return chipValues(field);
    }
    case "chips": return chipValues(field);
    default: return ctl.querySelector("input[type=text]").value;
  }
}

function collectFormValues() {
  const out = structuredClone(cfg);
  document.querySelectorAll("#cfg-form .field[data-path]").forEach((field) => {
    // backfill slider drives message AND attachment backfill (enabled + days mirror)
    if (field.dataset.widget === "backfill") {
      const stop = fieldValue(field);
      const on = stop !== "off";
      setNested(out, "discord.backfill_enabled", on);
      setNested(out, "attachments.backfill_enabled", on);
      if (on) {
        setNested(out, "discord.backfill_days", Number(stop));
        setNested(out, "attachments.backfill_days", Number(stop));
      }
      return;
    }
    setNested(out, field.dataset.path, fieldValue(field));
  });
  return out;
}

/* Save = collect edits, PUT, and surface the daemon's Config.validate()
   verdict (a 400 carries the error list). */
async function saveConfig() {
  const banner = document.getElementById("cfg-banner");
  banner.className = "cfg-banner";
  document.querySelectorAll(".field .err").forEach((e) => (e.style.display = "none"));
  document.querySelectorAll(".field input").forEach((i) => i.classList.remove("invalid"));

  const candidate = collectFormValues();
  document.getElementById("cfg-save").disabled = true;
  const r = await fetch(A("/config"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(candidate),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const errors = body.errors || ["validation failed"];
    for (const msg of errors) {
      // light the field whose key appears in the error, when findable
      const field = [...document.querySelectorAll(".field[data-path]")].find(
        (f) => msg.includes(f.dataset.path.split(".").at(-1)));
      const input = field?.querySelector("input, textarea, select");
      if (input) {
        input.classList.add("invalid");
        const err = field.querySelector(".err");
        if (err) { err.textContent = msg; err.style.display = "block"; }
      }
    }
    banner.className = "cfg-banner err";
    banner.textContent = `Validation failed — nothing was written. ${errors[0]}`;
    document.getElementById("cfg-save").disabled = false;
    return;
  }
  cfg = candidate;
  const saved = await r.json().catch(() => ({}));
  banner.className = "cfg-banner ok";
  banner.textContent = saved.restart_required
    ? "Saved — config validated and written. Restart the bot to apply it."
    : "Saved — config validated and written. It applies when you start the bot.";
  setDirty(false);
  // keep the rest of the dashboard honest without a manual reload (item 4):
  // refresh the always-visible nameplate and force config-dependent tabs to
  // reload fresh the next time they're opened.
  apiGet(A("/status")).then(renderNameplate).catch(() => {});
  loaded.monitor = false;
  loaded.memories = false;
}

/* ====================== file browser (shared) ====================== */

function makeBrowser(rootEl, treeData, kind) {
  const root = typeof rootEl === "string" ? document.getElementById(rootEl) : rootEl;
  root.innerHTML = `
    <div class="browser">
      <div class="tree"></div>
      <div class="pane"><div class="empty">Select a file to view it.</div></div>
    </div>`;
  const treeEl = root.querySelector(".tree");
  const paneEl = root.querySelector(".pane");
  treeEl.innerHTML = treeData.tree.map((n) => nodeHTML(n, 0)).join("");

  const open = (el) => {
    treeEl.querySelectorAll(".node.file").forEach((n) => n.classList.remove("active"));
    el.classList.add("active");
    showFile(paneEl, JSON.parse(el.dataset.file), kind);
  };

  treeEl.addEventListener("click", (e) => {
    const el = e.target.closest(".node");
    if (!el) return;
    if (el.classList.contains("dir")) {
      // keep the chevron and the children in lockstep
      el.classList.toggle("expanded");
      el.nextElementSibling?.classList.toggle("collapsed");
      return;
    }
    open(el);
  });

  // open to the first file so the pane is never empty
  const first = treeEl.querySelector(".node.file");
  if (first) open(first);
}

function nodeHTML(node, depth) {
  // ID-named entries resolve to a human label (channel/person/server); show that
  // as the name and keep the raw id as a muted subtitle. Non-ID names show as-is.
  const primary = esc(node.label || node.name);
  const sub = node.label ? `<span class="nid">${esc(node.name)}</span>` : "";
  if (node.type === "dir") {
    const kids = (node.children || []).map((c) => nodeHTML(c, depth + 1)).join("");
    // dirs start expanded; the chevron rotates with the .expanded state (CSS)
    return `<div class="node dir expanded"><span class="ic chev">▸</span>${primary}${sub}</div>
            <div class="children">${kids}</div>`;
  }
  return `<div class="node file" data-file='${esc(JSON.stringify(node)).replace(/'/g, "&#39;")}'>
            <span class="ic">·</span>${primary}${sub}</div>`;
}

const TEXT_KINDS = ["md", "txt", "json"];
const isTextFile = (node) =>
  TEXT_KINDS.includes(node.kind) || /\.(md|txt|json)(\.|$)/.test(node.name);

function fileMeta(node) {
  const size = node.size >= 1048576 ? (node.size / 1048576).toFixed(1) + " MB"
    : node.size >= 1024 ? (node.size / 1024).toFixed(0) + " KB" : node.size + " B";
  return `${size}${node.modified ? " · " + agoInWords(node.modified) : ""}`;
}

async function showFile(paneEl, node, kind) {
  // memories are hand-editable; repository files (often binary) are read-only here
  const editable = kind === "memory" && isTextFile(node);
  if (node.content == null) {
    if (!isTextFile(node)) {
      const del = kind === "repository"
        ? `<button class="pen danger del">Delete</button>` : "";
      paneEl.innerHTML = `<div class="crumb"><span class="path">${esc(node.path)}</span>
        <span class="meta figs">${fileMeta(node)}</span>${del}</div>
        <div class="empty">Binary file — view it on disk.</div>`;
      if (del) paneEl.querySelector(".del").onclick = async () => {
        if (!confirm(`Delete ${node.path} from the repository?`)) return;
        await apiSend("DELETE", A(`/repository/file?path=${encodeURIComponent(node.path)}`));
        loadRepository();
      };
      return;
    }
    paneEl.innerHTML = `<div class="empty">Loading…</div>`;
    try {
      const route = kind === "memory" ? "/memory/file" : "/repository/file";
      const resp = await apiGet(A(`${route}?path=${encodeURIComponent(node.path)}`));
      node.content = resp.content;
    } catch (e) {
      paneEl.innerHTML = `<div class="empty">Couldn't load ${esc(node.path)}.</div>`;
      return;
    }
  }
  renderView(paneEl, node, kind, editable);
}

function renderView(paneEl, node, kind, editable) {
  const isMd = node.name.endsWith(".md") || node.kind === "md";
  const body = isMd ? `<div class="md">${renderMarkdown(node.content)}</div>`
                    : `<pre>${esc(node.content)}</pre>`;
  const del = kind === "repository"
    ? `<button class="pen danger del">Delete</button>` : "";
  paneEl.innerHTML = `
    <div class="crumb">
      <span class="path">${esc(node.path)}</span>
      <span class="meta figs">${fileMeta(node)}</span>
      ${del}
      ${editable ? `<button class="pen">Edit</button>`
                 : `<button class="pen ro">read-only</button>`}
    </div>${body}`;
  if (editable) paneEl.querySelector(".pen:not(.del)").onclick = () => renderEditor(paneEl, node, kind);
  if (del) paneEl.querySelector(".del").onclick = async () => {
    if (!confirm(`Delete ${node.path} from the repository?`)) return;
    await apiSend("DELETE", A(`/repository/file?path=${encodeURIComponent(node.path)}`));
    loadRepository();
  };
}

function renderEditor(paneEl, node, kind) {
  paneEl.innerHTML = `
    <div class="crumb">
      <span class="path">${esc(node.path)}</span>
      <span class="meta">editing</span>
      <button class="pen cancel">Cancel</button>
      <button class="pen save">Save</button>
    </div>
    <textarea class="editor" spellcheck="false">${esc(node.content)}</textarea>`;
  const ta = paneEl.querySelector(".editor");
  paneEl.querySelector(".cancel").onclick = () => renderView(paneEl, node, kind, true);
  paneEl.querySelector(".save").onclick = async () => {
    const btn = paneEl.querySelector(".save");
    btn.textContent = "Saving…"; btn.disabled = true;
    await apiSend("PUT", A(`/memory/file?path=${encodeURIComponent(node.path)}`), { content: ta.value });
    node.content = ta.value;                       // mock: persist into the loaded tree
    node.modified = new Date().toISOString();
    node.size = ta.value.length;
    renderView(paneEl, node, kind, true);
    paneEl.querySelector(".crumb").insertAdjacentHTML("beforeend", `<span class="saved">saved ✓</span>`);
  };
}

/* tiny markdown: headings, lists, bold/italic, [[wikilinks]], paragraphs */
function renderMarkdown(src) {
  const lines = src.split("\n");
  let html = "", inList = false;
  const inline = (s) => esc(s)
    .replace(/\[\[([^\]]+)\]\]/g, (_, w) => `<a class="wikilink">${w.split("/").pop()}</a>`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const ln of lines) {
    if (/^#\s/.test(ln)) { html += closeList(); html += `<h1>${inline(ln.slice(2))}</h1>`; }
    else if (/^##\s/.test(ln)) { html += closeList(); html += `<h2>${inline(ln.slice(3))}</h2>`; }
    else if (/^###\s/.test(ln)) { html += closeList(); html += `<h3>${inline(ln.slice(4))}</h3>`; }
    else if (/^[-*]\s/.test(ln)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(ln.slice(2))}</li>`; }
    else if (/^>\s/.test(ln)) { html += closeList(); html += `<p><em>${inline(ln.slice(2))}</em></p>`; }
    else if (ln.trim() === "") { html += closeList(); }
    else { html += closeList(); html += `<p>${inline(ln)}</p>`; }
  }
  function closeList() { if (inList) { inList = false; return "</ul>"; } return ""; }
  return html + closeList();
}

/* ---- Memories: browser + new-file + induction (memory pre-population) ---- */

async function loadMemories() {
  const panel = document.getElementById("tab-memories");
  const [tree, status] = await Promise.all([
    apiGet(A("/memory/tree")), apiGet(A("/status"))]);
  const serverOpts = (status.servers || []).map((s) =>
    `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
  panel.innerHTML = `
    <div class="induct">
      <div class="ihead">
        <h3>Build starting memory</h3>
        <span class="sub">induction — distill a server's stored history into
          memory the bot starts with</span>
      </div>
      <div class="irow">
        <select id="ind-server">${serverOpts || `<option value="">no servers configured</option>`}</select>
        <button class="btn small" id="ind-dry">Preview scope &amp; cost</button>
        <button class="btn small primary" id="ind-run">Run induction</button>
        <span class="inote">uses the memory model from Configure · the bot must
          be stopped${status.running ? " — <b>it is running now</b>" : ""}</span>
      </div>
      <pre class="iout" id="ind-out" hidden></pre>
    </div>
    <div class="browse-bar">
      <button class="btn small" id="mem-new">+ New memory file</button>
      <span class="sub">memory files are the bot's own notes — edits land
        before its next turn</span>
    </div>
    <div id="mem-browser"></div>`;
  makeBrowser(document.getElementById("mem-browser"), tree, "memory");
  document.getElementById("mem-new").onclick = newMemoryFile;
  document.getElementById("ind-dry").onclick = () => startInduct(true);
  document.getElementById("ind-run").onclick = () => startInduct(false);
  pollInduct(false); // surface an induction already in flight
}

function newMemoryFile() {
  const dlg = document.getElementById("dlg-memfile");
  const path = document.getElementById("memfile-path");
  const body = document.getElementById("memfile-body");
  const err = document.getElementById("memfile-err");
  path.value = ""; body.value = ""; err.style.display = "none";
  dlg.showModal();
  dlg.querySelector("form").onsubmit = async (e) => {
    e.preventDefault();
    const rel = path.value.trim().replace(/^\/+/, "");
    if (!rel) { err.textContent = "give the file a path"; err.style.display = "block"; return; }
    try {
      await apiSend("PUT", A(`/memory/file?path=${encodeURIComponent(rel)}`),
        { content: body.value });
    } catch (ex) {
      err.textContent = ex.message; err.style.display = "block"; return;
    }
    dlg.close();
    loadMemories();
  };
}

let inductTimer = null;
async function startInduct(dryRun) {
  const server = document.getElementById("ind-server").value;
  const out = document.getElementById("ind-out");
  if (!server) return;
  if (!dryRun && !confirm(
    "Run a full induction? This writes starting-memory files for the " +
    "selected server (existing memory files are not overwritten silently — " +
    "per-channel failures write nothing). It uses the Batches API and can " +
    "take a while.")) return;
  out.hidden = false;
  out.textContent = dryRun ? "previewing…" : "induction starting…";
  try {
    const r = await fetch(A("/induct"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server, dry_run: dryRun }),
    });
    const bodyJson = await r.json().catch(() => ({}));
    if (!r.ok) { out.textContent = bodyJson.error || `failed (${r.status})`; return; }
  } catch (e) { out.textContent = String(e); return; }
  pollInduct(true);
}

async function pollInduct(loud) {
  clearTimeout(inductTimer);
  const out = document.getElementById("ind-out");
  if (!out) return;
  let st;
  try { st = await apiGet(A("/induct")); } catch { return; }
  if (!st.lines.length && !st.running && !loud) return; // nothing to show
  out.hidden = false;
  out.textContent = st.lines.join("\n") || (st.running ? "working…" : "");
  out.scrollTop = out.scrollHeight;
  if (st.running) inductTimer = setTimeout(() => pollInduct(true), 2500);
  else if (loud) {
    out.textContent += st.returncode === 0
      ? "\n\n— done." : `\n\n— exited with code ${st.returncode}.`;
  }
}

/* ---- Repository: a small Finder — upload, new folder, rename, drag-move ---- */

const baseName = (p) => p.split("/").pop();
const parentDir = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
const joinPath = (dir, name) => (dir ? `${dir}/${name}` : name);

let repoSel = "";   // selected folder = the upload target ("" is the root)

async function loadRepository() {
  const panel = document.getElementById("tab-repository");
  const tree = await apiGet(A("/repository/tree"));
  // keep the selection only if that folder still exists
  if (repoSel && !findRepoDir(tree.tree || [], repoSel)) repoSel = "";
  panel.innerHTML = `
    <div class="repo-bar">
      <button class="btn small" id="repo-upload">Upload file</button>
      <button class="btn small" id="repo-newdir">New folder</button>
      <button class="btn small danger" id="repo-deldir" hidden>Delete folder</button>
      <span class="repo-target">Upload to <b id="repo-target"></b></span>
      <span class="err" id="repo-err"></span>
      <input type="file" id="repo-file" multiple hidden>
    </div>
    <div class="repo-hint">Drag files and folders to move them · double-click a
      name to rename · drop onto the root strip to move out of a folder</div>
    <div class="browser">
      <div class="tree" id="repo-tree">
        <div class="rootzone" data-path="">Repository root</div>
        ${(tree.tree || []).map(repoNodeHTML).join("")
          || `<div class="empty" style="padding:18px 8px">Empty — upload a file or make a folder.</div>`}
      </div>
      <div class="pane"><div class="empty">Select a file to view it.</div></div>
    </div>`;
  wireRepo(panel, tree);
}

function repoNodeHTML(node) {
  const tag = `draggable="true" data-path="${esc(node.path)}" data-type="${node.type}"`;
  if (node.type === "dir") {
    const kids = (node.children || []).map(repoNodeHTML).join("");
    return `<div class="node dir expanded droppable ${repoSel === node.path ? "selected" : ""}" ${tag}>
        <span class="ic chev">▸</span><span class="nm">${esc(node.name)}</span>
      </div><div class="children">${kids}</div>`;
  }
  return `<div class="node file" ${tag}
        data-file='${esc(JSON.stringify(node)).replace(/'/g, "&#39;")}'>
      <span class="ic">·</span><span class="nm">${esc(node.name)}</span></div>`;
}

function findRepoDir(nodes, path) {
  for (const n of nodes) {
    if (n.type !== "dir") continue;
    if (n.path === path) return n;
    const hit = findRepoDir(n.children || [], path);
    if (hit) return hit;
  }
  return null;
}

function wireRepo(panel, tree) {
  const treeEl = panel.querySelector("#repo-tree");
  const paneEl = panel.querySelector(".pane");
  const err = panel.querySelector("#repo-err");
  const fileIn = panel.querySelector("#repo-file");
  const setErr = (m) => { err.textContent = m || ""; };

  const syncTarget = () => {
    panel.querySelector("#repo-target").textContent = repoSel ? `/${repoSel}` : "/ (root)";
    panel.querySelector("#repo-deldir").hidden = !repoSel;
  };
  syncTarget();

  // selection + expand/collapse + open file
  treeEl.addEventListener("click", (e) => {
    if (e.target.closest(".nm.editing")) return;
    const el = e.target.closest(".node");
    if (!el) {
      if (e.target.classList.contains("rootzone")) { repoSel = ""; reselect(); }
      return;
    }
    if (el.classList.contains("dir")) {
      if (e.target.closest(".chev")) {          // chevron toggles only
        el.classList.toggle("expanded");
        el.nextElementSibling?.classList.toggle("collapsed");
        return;
      }
      repoSel = el.dataset.path; reselect();     // body selects as upload target
      return;
    }
    treeEl.querySelectorAll(".node.file").forEach((n) => n.classList.remove("active"));
    el.classList.add("active");
    showFile(paneEl, JSON.parse(el.dataset.file), "repository");
  });
  function reselect() {
    treeEl.querySelectorAll(".node.dir").forEach((n) =>
      n.classList.toggle("selected", n.dataset.path === repoSel));
    panel.querySelector(".rootzone").classList.toggle("selected", repoSel === "");
    syncTarget();
  }

  // inline rename (double-click the name)
  treeEl.addEventListener("dblclick", (e) => {
    const nm = e.target.closest(".nm");
    const node = e.target.closest(".node");
    if (!nm || !node) return;
    beginRename(nm, node);
  });
  function beginRename(nm, node) {
    const oldName = nm.textContent;
    const oldPath = node.dataset.path;
    nm.classList.add("editing");
    nm.innerHTML = `<input class="rename-in" value="${esc(oldName)}">`;
    const inp = nm.querySelector("input");
    inp.focus(); inp.select();
    let done = false;
    const finish = async (commit) => {
      if (done) return; done = true;
      const next = inp.value.trim();
      nm.classList.remove("editing");
      if (!commit || !next || next === oldName) { nm.textContent = oldName; return; }
      nm.textContent = next;
      try {
        await apiSend("POST", A("/repository/move"),
          { from: oldPath, to: joinPath(parentDir(oldPath), next) });
        loadRepository();
      } catch (ex) { setErr("rename failed — name may already exist"); loadRepository(); }
    };
    inp.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") finish(true);
      else if (ev.key === "Escape") finish(false);
    });
    inp.addEventListener("blur", () => finish(true));
  }

  // drag to move (files and folders), into folders or out to the root strip
  let dragPath = null, dragType = null;
  treeEl.addEventListener("dragstart", (e) => {
    const el = e.target.closest(".node");
    if (!el) return;
    dragPath = el.dataset.path; dragType = el.dataset.type;
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", dragPath); } catch {}
  });
  const dropTarget = (e) => e.target.closest(".node.dir.droppable") || e.target.closest(".rootzone");
  treeEl.addEventListener("dragover", (e) => {
    if (dragPath == null) return;
    const t = dropTarget(e);
    if (!t) return;
    e.preventDefault();
    treeEl.querySelectorAll(".dropping").forEach((n) => n.classList.remove("dropping"));
    t.classList.add("dropping");
  });
  treeEl.addEventListener("dragleave", (e) => {
    const t = dropTarget(e);
    if (t) t.classList.remove("dropping");
  });
  treeEl.addEventListener("drop", async (e) => {
    const t = dropTarget(e);
    treeEl.querySelectorAll(".dropping").forEach((n) => n.classList.remove("dropping"));
    if (dragPath == null || !t) return;
    e.preventDefault();
    const destDir = t.dataset.path || "";
    const from = dragPath; dragPath = null;
    if (destDir === parentDir(from)) return;          // already there — no-op
    if (dragType === "dir" && (destDir === from || destDir.startsWith(from + "/"))) {
      setErr("can't move a folder into itself"); return;
    }
    try {
      await apiSend("POST", A("/repository/move"),
        { from, to: joinPath(destDir, baseName(from)) });
      loadRepository();
    } catch (ex) { setErr("move failed — a file with that name may already be there"); }
  });

  // toolbar: upload (to the selected folder), new folder, delete folder
  panel.querySelector("#repo-upload").onclick = () => { setErr(""); fileIn.click(); };
  fileIn.onchange = async () => {
    const files = [...fileIn.files];
    if (!files.length) return;
    const btn = panel.querySelector("#repo-upload");
    btn.disabled = true; btn.textContent = "Uploading…";
    try {
      for (const f of files) {
        const r = await fetch(A(`/repository/file?path=${encodeURIComponent(joinPath(repoSel, f.name))}`),
          { method: "PUT", body: f });
        if (!r.ok) {
          const b = await r.json().catch(() => ({}));
          throw new Error(b.error || `upload failed (${r.status})`);
        }
      }
      loadRepository();
    } catch (ex) {
      setErr(ex.message);
      btn.disabled = false; btn.textContent = "Upload file";
    }
  };
  panel.querySelector("#repo-newdir").onclick = async () => {
    setErr("");
    // mint a unique "untitled folder" then drop it straight into rename
    const existing = new Set((tree.tree || []).filter((n) => n.type === "dir")
      .map((n) => n.name));
    let name = "untitled folder", i = 2;
    while (existing.has(name)) name = `untitled folder ${i++}`;
    try {
      await apiSend("POST", A("/repository/dir"), { path: joinPath(repoSel, name) });
      await loadRepository();
      const node = [...document.querySelectorAll("#repo-tree .node.dir")]
        .find((n) => n.dataset.path === joinPath(repoSel, name));
      if (node) beginRename(node.querySelector(".nm"), node);
    } catch (ex) { setErr("couldn't create folder"); }
  };
  panel.querySelector("#repo-deldir").onclick = async () => {
    if (!repoSel) return;
    if (!confirm(`Delete the folder "${repoSel}" and everything in it?`)) return;
    try {
      await apiSend("DELETE", A(`/repository/file?path=${encodeURIComponent(repoSel)}`));
      repoSel = "";
      loadRepository();
    } catch (ex) { setErr("couldn't delete folder"); }
  };

  // open the first file so the pane isn't blank
  const first = treeEl.querySelector(".node.file");
  if (first) { first.classList.add("active"); showFile(paneEl, JSON.parse(first.dataset.file), "repository"); }
}

/* ====================== Integrations (skills + MCP) ====================== */

let intl = null;            // the loaded integrations payload
let intPending = [];        // human-readable pending changes (require restart)

async function loadIntegrations() {
  intl = await apiGet(A("/integrations"));
  intPending = [];
  renderSkills(); renderMcp(); renderRestartBar();
  wireIntegrations();
}

function markPending(desc) {
  intPending.push(desc);
  renderRestartBar();
}

function renderSkills() {
  const on = intl.skills.filter((s) => s.enabled).length;
  document.getElementById("skills-count").textContent = `${on} of ${intl.skills.length} enabled`;
  document.getElementById("skills-list").innerHTML = intl.skills.map((s) => `
    <div class="skill ${s.enabled ? "on" : ""}" data-skill="${esc(s.id)}">
      <div class="sw"></div>
      <div class="meta">
        <div class="name">${esc(s.name)}<span class="src ${s.source === "custom" ? "custom" : ""}">${esc(s.source)}</span></div>
        <div class="desc">${esc(s.description)}</div>
      </div>
      <div class="use">${s.enabled ? `${s.used_7d} uses · 7d` : "disabled"}</div>
      ${s.source === "custom" ? `<button class="srm" data-act="remove" title="Delete this skill from disk">Remove</button>` : ""}
    </div>`).join("");
}

const MCP_STATE = { connected: "Connected", error: "Error", connecting: "Connecting…", disabled: "Disabled" };

function mcpCard(m) {
  const right = m.status === "connected"
    ? `${m.latency_ms}ms · ${m.tools.length} tools<br>checked ${agoInWords(m.last_check)}`
    : `checked ${agoInWords(m.last_check)}`;
  return `
    <div class="mcp ${m.status === "error" ? "error" : ""}" data-mcp="${esc(m.name)}">
      <div class="top">
        <span class="stat ${m.status}"><span class="led"></span>${MCP_STATE[m.status] || m.status}</span>
        <div class="ident">
          <div class="name">${esc(m.name)}</div>
          <div class="conn"><span class="tp">${esc(m.transport)}</span>${esc(m.target)}</div>
        </div>
        <div class="meta-r">${right}</div>
      </div>
      <div class="body">
        ${m.error ? `<div class="err">${esc(m.error)}</div>` : ""}
        ${m.tools.length ? `<div class="tools"><span class="tlabel">tools</span>${m.tools.map((t) => `<span class="tool">${esc(t)}</span>`).join("")}</div>` : ""}
        <div class="acts">
          ${m.status === "error" ? `<button data-act="reconnect">Reconnect</button>` : ""}
          <button data-act="edit">Edit</button>
          <button class="danger" data-act="remove">Remove</button>
        </div>
      </div>
    </div>`;
}

function renderMcp() {
  const live = intl.mcp_servers.filter((m) => m.status === "connected").length;
  document.getElementById("mcp-count").textContent = `${live} of ${intl.mcp_servers.length} connected`;
  document.getElementById("mcp-list").innerHTML = intl.mcp_servers.map(mcpCard).join("");
}

function renderRestartBar() {
  const bar = document.getElementById("restart-bar");
  if (!intPending.length) { bar.className = "restart-bar"; return; }
  bar.className = "restart-bar show";
  const n = intPending.length;
  document.getElementById("restart-msg").innerHTML =
    `<b>${n} pending change${n > 1 ? "s" : ""}</b> — saving restarts <b>${botId}</b> to apply ${n > 1 ? "them" : "it"}.`;
}

let intWired = false;
function wireIntegrations() {
  if (intWired) return; intWired = true;

  document.getElementById("skills-list").addEventListener("click", async (e) => {
    const row = e.target.closest(".skill");
    if (!row) return;
    const s = intl.skills.find((x) => x.id === row.dataset.skill);
    // Remove = delete the skill from disk (shared across bots), now — not staged.
    if (e.target.closest('[data-act="remove"]')) {
      if (!confirm(`Remove the skill “${s.name}” from disk?\n\nSkills live in one shared folder, so this removes it for every bot. It takes effect on each bot's next start.`)) return;
      try {
        await apiSend("DELETE", A(`/skills/${encodeURIComponent(s.name)}`));
        intl.skills = intl.skills.filter((x) => x.id !== s.id);
        renderSkills();
      } catch (ex) { alert("Couldn't remove the skill — see the supervisor log."); }
      return;
    }
    s.enabled = !s.enabled;
    markPending(`${s.enabled ? "enable" : "disable"} skill “${s.name}”`);
    renderSkills();
  });

  document.getElementById("mcp-list").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const name = btn.closest(".mcp").dataset.mcp;
    const m = intl.mcp_servers.find((x) => x.name === name);
    if (btn.dataset.act === "remove") {
      intl.mcp_servers = intl.mcp_servers.filter((x) => x.name !== name);
      markPending(`remove server “${name}”`);
      renderMcp();
    } else if (btn.dataset.act === "reconnect") {
      reconnect(m);   // live action — no restart needed
    } else if (btn.dataset.act === "edit") {
      openMcpDialog(m);
    }
  });

  document.getElementById("mcp-add").addEventListener("click", () => openMcpDialog(null));
  document.getElementById("skill-add").addEventListener("click", openSkillDialog);
  document.getElementById("restart-discard").addEventListener("click", () => loadIntegrations());
  document.getElementById("restart-save").addEventListener("click", saveAndRestart);

  // transport → relabel the target field
  const tr = document.getElementById("mcp-transport");
  tr.addEventListener("change", () => {
    document.getElementById("mcp-target-label").textContent = tr.value === "stdio" ? "Command" : "URL";
    document.getElementById("mcp-target").placeholder = tr.value === "stdio" ? "python -m my_mcp_server" : "https://…";
  });
}

function openSkillDialog() {
  const dlg = document.getElementById("dlg-skill");
  const input = document.getElementById("skill-file");
  const err = document.getElementById("skill-err");
  input.value = ""; err.style.display = "none";
  dlg.showModal();
  dlg.querySelector("form").onsubmit = (e) => {
    e.preventDefault();
    const file = input.files && input.files[0];
    if (!file) { err.textContent = "choose a skill file or folder first"; err.style.display = "block"; return; }
    const base = file.name.replace(/\.(zip|skill)$/i, "");
    const id = base.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    if (intl.skills.some((s) => s.id === id)) {
      err.textContent = `a skill named “${base}” is already installed`; err.style.display = "block"; return;
    }
    dlg.close();
    intl.skills.push({ id, name: base, source: "custom", enabled: true,
      description: `Added from ${file.name} — loads on next start.`, used_7d: 0 });
    markPending(`add skill “${base}”`);
    renderSkills();
  };
}

async function reconnect(m) {
  m.status = "connecting"; m.error = null; renderMcp();
  await apiSend("POST", A(`/mcp/${encodeURIComponent(m.name)}/reconnect`));
  m.status = "connected"; m.latency_ms = 30 + (m.name.length % 40);
  m.tools = m.tools.length ? m.tools : ["ping"];
  m.last_check = new Date().toISOString();
  renderMcp();
}

function openMcpDialog(existing) {
  const dlg = document.getElementById("dlg-mcp");
  const err = document.getElementById("mcp-name-err");
  err.style.display = "none";
  document.getElementById("mcp-name").value = existing ? existing.name : "";
  document.getElementById("mcp-transport").value = existing ? existing.transport : "stdio";
  document.getElementById("mcp-target").value = existing ? existing.target : "";
  document.getElementById("mcp-env").value = existing ? (existing.env || []).join(", ") : "";
  document.getElementById("mcp-target-label").textContent =
    (existing ? existing.transport : "stdio") === "stdio" ? "Command" : "URL";
  dlg.showModal();
  dlg.querySelector("form").onsubmit = (e) => {
    e.preventDefault();
    const name = document.getElementById("mcp-name").value.trim();
    const dup = intl.mcp_servers.some((x) => x.name === name && x !== existing);
    if (!name || dup) {
      err.textContent = dup ? `a server named “${name}” already exists` : "give the server a name";
      err.style.display = "block";
      return;
    }
    const fields = {
      name,
      transport: document.getElementById("mcp-transport").value,
      target: document.getElementById("mcp-target").value.trim(),
      env: document.getElementById("mcp-env").value.split(/[,\n]/).map((s) => s.trim()).filter(Boolean),
    };
    dlg.close();
    if (existing) {
      Object.assign(existing, fields);
      markPending(`edit server “${name}”`);
    } else {
      intl.mcp_servers.push({ ...fields, status: "connecting", latency_ms: null, last_check: new Date().toISOString(), tools: [] });
      markPending(`add server “${name}”`);
    }
    renderMcp();
  };
}

async function saveAndRestart() {
  const bar = document.getElementById("restart-bar");
  const save = document.getElementById("restart-save");
  save.disabled = true;
  document.getElementById("restart-msg").innerHTML = `Restarting <b>${botId}</b>…`;
  await apiSend("PUT", A("/integrations"), intl);
  await new Promise((r) => setTimeout(r, 900));
  // connecting servers come up after the restart
  intl.mcp_servers.forEach((m) => {
    if (m.status === "connecting") {
      m.status = "connected"; m.latency_ms = 25 + (m.name.length % 35);
      m.tools = m.tools.length ? m.tools : ["ping"]; m.last_check = new Date().toISOString();
    }
  });
  intPending = [];
  renderMcp(); renderSkills();
  const on = intl.skills.filter((s) => s.enabled).length;
  const live = intl.mcp_servers.filter((m) => m.status === "connected").length;
  bar.className = "restart-bar show done";
  document.getElementById("restart-msg").innerHTML =
    `<b>${botId} back online</b> — ${on} skills, ${live} MCP servers loaded.`;
  save.disabled = false;
  setTimeout(() => { if (!intPending.length) bar.className = "restart-bar"; }, 4000);
}

/* ============================ wiring ============================ */

const LOADERS = {
  monitor: loadMonitor,
  configure: loadConfigure,
  integrations: loadIntegrations,
  memories: loadMemories,
  repository: loadRepository,
};

boot();
