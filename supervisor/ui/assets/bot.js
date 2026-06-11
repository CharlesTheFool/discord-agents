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
      <div class="v figs">${tokens(t.cache_read + t.uncached_in + t.out)}</div>
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

/* Optional schema hints — the real form walks the config generically and
   consults these (sourced from Config.validate) only for labels, help,
   enums, and which strings are textareas. Unknown keys still render. */
/* Enums/labels/help mirror core/config.py (validate()) + internal_constants
   presets. The form still walks the config generically — unknown keys render
   from inferred type; HINTS only enrich. */
const HINTS = {
  "discord.servers": { label: "Servers", help: "Discord server (guild) ids this bot lives in.", widget: "chips" },
  "discord.status": { label: "Status", help: "Activity line shown under the bot in Discord." },
  "discord.timezone": { label: "Timezone", help: "IANA tz; drives quiet-hours and timestamps." },
  "discord.allow_bot_interactions": { label: "Reply to other bots", help: "Whether other bots can trigger this one." },
  "discord.backfill_enabled": { label: "Backfill history", help: "Pull prior channel history on first join." },
  "discord.backfill_days": { label: "Backfill days", help: "Days of history to pull. 0 = unlimited." },

  "personality.base_prompt": { label: "Personality", help: "The standing prompt — who this bot is.", widget: "prompt" },
  "personality.reaction_usage": { label: "Reaction usage", options: ["never", "rare", "moderate", "frequent"], help: "How often it adds emoji reactions." },

  "reactive.enabled": { label: "Reactive engine", help: "Answers @mentions and scans conversation." },
  "reactive.always_respond_to_mentions": { label: "Always answer @mentions", help: "Guarantees a reply when pinged." },
  "reactive.rate_limit": { label: "Rate limit", options: ["strict", "moderate", "permissive", "unlimited"], help: "Preset governing how often it answers." },
  "reactive.check_interval_seconds": { label: "Scan interval", help: "Seconds between periodic conversation scans.", min: 1 },

  "agentic.enabled": { label: "Agentic engine", help: "The background loop (proactive + follow-ups)." },
  "agentic.check_interval_hours": { label: "Loop interval", help: "Hours between agentic wake-ups." },
  "agentic.followups.enabled": { label: "Follow-ups", help: "Lets the bot schedule and fire follow-ups." },
  "agentic.proactive.enabled": { label: "Proactive engagement", help: "Opening conversations unprompted." },
  "agentic.proactive.intensity": { label: "Intensity", options: ["gentle", "moderate", "active"], help: "Preset: idle window + per-day caps." },
  "agentic.proactive.quiet_hours": { label: "Quiet hours", help: "Local-clock hours (0–23) it stays silent.", widget: "chips" },
  "agentic.proactive.allowed_channels": { label: "Allowed channels", help: "Where proactive openings are permitted. Empty = none.", widget: "chips" },

  "api.model": { label: "Model", options: ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-8"] },
  "api.max_tokens": { label: "Max tokens", help: "Cap on tokens per response." },
  "api.context_messages": { label: "Context messages", help: "Live messages kept before reseed (5–100).", min: 5, max: 100 },
  "api.context_tokens": { label: "Context ceiling", help: "Token budget before distill + reseed." },
  "api.effort": { label: "Effort", options: ["low", "medium", "high", "max"], help: "Cost dial. Rejected at startup on models without effort support." },
  "api.consolidation_model": { label: "Consolidation model", options: ["claude-sonnet-4-6", "claude-haiku-4-5"], help: "Model used to distill episodes." },
  "api.thinking.enabled": { label: "Extended thinking", help: "Adaptive thinking; the model decides when." },
  "api.web_search.enabled": { label: "Web search", help: "All-or-nothing live web lookup tool." },

  "mcp.enabled": { label: "MCP servers", help: "Load Model Context Protocol servers (see Integrations)." },
  "skills.include_anthropic_skills": { label: "Built-in skills", help: "Include the Anthropic skill set (pdf, xlsx, …)." },
  "skills.default_skills": { label: "Default skills", help: "Loaded at start; the bot can request others mid-turn.", widget: "chips" },

  "attachments.enabled": { label: "Attachments", help: "Unified file/image handling." },
  "attachments.backfill_enabled": { label: "Backfill attachments", help: "Pull prior attachments on first join." },
  "attachments.backfill_days": { label: "Attachment backfill days", help: "0 = unlimited." },
  "attachments.repository.enabled": { label: "File repository", help: "Per-server drive the bot manages." },

  "logging.level": { label: "Log level", options: ["DEBUG", "INFO", "WARNING", "ERROR"] },

  "vaults": { label: "Vaults", help: "Channel/server ids whose content never leaves them. Empty = none.", widget: "chips" },
};
const GROUP_NOTES = {
  personality: "The single most load-bearing field. Changes here reshape every reply.",
  mcp: "Servers are added and monitored on the Integrations tab.",
};

let cfg = null, cfgDirty = false;

async function loadConfigure() {
  cfg = await apiGet(A("/config"));
  const form = document.getElementById("cfg-form");
  buildForm();
  form.addEventListener("input", () => setDirty(true));
  form.addEventListener("change", (e) => {
    if (e.target.matches('.toggle input')) {
      e.target.parentElement.lastChild.textContent = ` ${e.target.checked ? "enabled" : "disabled"}`;
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
  document.getElementById("cfg-form").innerHTML =
    Object.keys(cfg).map((section) => groupHTML(section, cfg[section])).join("");
}

function groupHTML(section, val) {
  const note = GROUP_NOTES[section] ? `<div class="note">${GROUP_NOTES[section]}</div>` : "";
  return `<div class="cfg-group"><h3>${section}</h3>${note}${walk(val, section)}</div>`;
}

/* Recursively render a config subtree into fields. */
function walk(val, path) {
  if (Array.isArray(val) || typeof val !== "object" || val === null)
    return fieldHTML(path, val);
  return Object.keys(val).map((k) => walk(val[k], `${path}.${k}`)).join("");
}

function fieldHTML(path, val) {
  const h = HINTS[path] || {};
  const label = h.label || path.split(".").pop().replace(/_/g, " ");
  const widget = h.widget || inferWidget(val);
  const lab = `<div class="lab">
      <div class="name">${esc(label)}</div>
      <div class="path">${esc(path)}</div>
      ${h.help ? `<div class="help">${esc(h.help)}</div>` : ""}
    </div>`;
  return `<div class="field" data-path="${esc(path)}">${lab}<div class="ctl">${ctlHTML(widget, val, h)}</div></div>`;
}

function inferWidget(val) {
  if (typeof val === "boolean") return "toggle";
  if (typeof val === "number") return "number";
  if (Array.isArray(val)) return "chips";
  if (typeof val === "string" && val.length > 80) return "prompt";
  return "text";
}

function ctlHTML(widget, val, h) {
  switch (widget) {
    case "toggle":
      return `<label class="toggle"><input type="checkbox" ${val ? "checked" : ""}> ${val ? "enabled" : "disabled"}</label>`;
    case "prompt":
      return `<textarea class="prompt">${esc(String(val))}</textarea>`;
    case "number":
      return `<input type="number" value="${esc(String(val))}" ${h.min != null ? `min="${h.min}"` : ""} ${h.max != null ? `max="${h.max}"` : ""}><div class="err"></div>`;
    case "chips": {
      const items = Array.isArray(val) ? val : [];
      const chips = items.length
        ? items.map((x) => `<span class="chip">${esc(String(x))}</span>`).join("")
        : `<span class="empty">none</span>`;
      return `<div class="chips">${chips}</div>`;
    }
    default:
      if (h.options)
        return `<select>${h.options.map((o) =>
          `<option ${o === val ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
      return `<input type="text" value="${esc(String(val))}"><div class="err"></div>`;
  }
}

function setDirty(d) {
  cfgDirty = d;
  document.getElementById("cfg-dirty").textContent = d ? "unsaved changes" : "";
  document.getElementById("cfg-save").disabled = !d;
}

/* Collect the edited form back into a config object (chips stay read-only
   and keep their loaded values). */
function setNested(obj, path, value) {
  const keys = path.split(".");
  let cur = obj;
  for (const k of keys.slice(0, -1)) cur = (cur[k] = cur[k] ?? {});
  cur[keys.at(-1)] = value;
}

function collectFormValues() {
  const out = structuredClone(cfg);
  document.querySelectorAll(".field[data-path]").forEach((field) => {
    const path = field.dataset.path;
    const ctl = field.querySelector(".ctl");
    const toggle = ctl.querySelector(".toggle input");
    if (toggle) return setNested(out, path, toggle.checked);
    const prompt = ctl.querySelector("textarea.prompt");
    if (prompt) return setNested(out, path, prompt.value);
    const select = ctl.querySelector("select");
    if (select) return setNested(out, path, select.value);
    const num = ctl.querySelector('input[type="number"]');
    if (num) return setNested(out, path, Number(num.value));
    const text = ctl.querySelector('input[type="text"]');
    if (text) return setNested(out, path, text.value);
    // chips and anything unrecognized: keep the loaded value
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
  banner.className = "cfg-banner ok";
  banner.textContent = "Saved — config validated and written. Restart the bot to apply it.";
  setDirty(false);
}

/* ====================== file browser (shared) ====================== */

function makeBrowser(panelId, treeData, kind) {
  const root = document.getElementById(panelId);
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
  const label = node.label ? `<span class="label">${esc(node.label)}</span>` : "";
  if (node.type === "dir") {
    const kids = (node.children || []).map((c) => nodeHTML(c, depth + 1)).join("");
    return `<div class="node dir"><span class="ic">▸</span>${esc(node.name)}${label}</div>
            <div class="children">${kids}</div>`;
  }
  return `<div class="node file" data-file='${esc(JSON.stringify(node)).replace(/'/g, "&#39;")}'>
            <span class="ic">·</span>${esc(node.name)}${label}</div>`;
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
      paneEl.innerHTML = `<div class="crumb"><span class="path">${esc(node.path)}</span>
        <span class="meta figs">${fileMeta(node)}</span></div>
        <div class="empty">Binary file — view it on disk.</div>`;
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
  paneEl.innerHTML = `
    <div class="crumb">
      <span class="path">${esc(node.path)}</span>
      <span class="meta figs">${fileMeta(node)}</span>
      ${editable ? `<button class="pen">Edit</button>`
                 : `<button class="pen ro">read-only</button>`}
    </div>${body}`;
  if (editable) paneEl.querySelector(".pen").onclick = () => renderEditor(paneEl, node, kind);
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

async function loadMemories() {
  const tree = await apiGet(A("/memory/tree"));
  makeBrowser("tab-memories", tree, "memory");
}
async function loadRepository() {
  const tree = await apiGet(A("/repository/tree"));
  makeBrowser("tab-repository", tree, "repository");
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

  document.getElementById("skills-list").addEventListener("click", (e) => {
    const row = e.target.closest(".skill");
    if (!row) return;
    const s = intl.skills.find((x) => x.id === row.dataset.skill);
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
