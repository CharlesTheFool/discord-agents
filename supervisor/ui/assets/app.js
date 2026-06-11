/* Discord Agents — Supervisor UI: API layer + shared helpers.
   Callers always use the REAL route strings ("/api/bots", ...).
   MOCK=true reroutes GETs to static JSON files and fakes writes locally;
   pointing at the live daemon is the one-line MOCK=false switch. */

const API_BASE = "";
const MOCK = false;

/* Map a real route to its static mock file:
   /api/bots                        -> /api/bots.json
   /api/bots/x/logs?file=main&...   -> /api/bots/x/logs-main.json */
function mockUrl(path) {
  const [p, q] = path.split("?");
  if (!q) return p + ".json";
  const file = new URLSearchParams(q).get("file");
  return file ? `${p}-${file}.json` : p + ".json";
}

async function apiGet(path) {
  const url = MOCK ? mockUrl(path) : API_BASE + path;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}`);
  return r.json();
}

/* Writes (start/stop/create/delete/config PUT). In mock mode they succeed
   after a short beat so the UI's pending states are visible. */
async function apiSend(method, path, body) {
  if (MOCK) return new Promise((res) => setTimeout(() => res({ ok: true }), 350));
  const r = await fetch(API_BASE + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}`);
  return r.json().catch(() => ({}));
}

/* ---------- prose helpers (the Ledger voice) ---------- */

const WORDS = ["zero", "one", "two", "three", "four", "five", "six",
  "seven", "eight", "nine", "ten", "eleven", "twelve"];
const numWord = (n) => (n >= 0 && n < WORDS.length ? WORDS[n] : String(n));

const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

function clockTime(d) {
  if (!d || isNaN(d.getTime?.() ?? NaN)) return "—";
  let h = d.getHours();
  const ampm = h >= 12 ? "p.m." : "a.m.";
  h = h % 12 || 12;
  return `${h}:${String(d.getMinutes()).padStart(2, "0")} ${ampm}`;
}

function longDate(d) {
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${clockTime(d)}`;
}

function agoInWords(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 90) return "moments ago";
  const m = Math.round(s / 60);
  if (m < 60) return `${numWord(m)} minutes ago`;
  const h = Math.round(m / 60);
  if (h < 24) return h === 1 ? "an hour ago" : `${numWord(h)} hours ago`;
  return "on " + longDate(new Date(iso));
}

function uptimeInWords(sec) {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d} day${d > 1 ? "s" : ""}, ${h} hour${h !== 1 ? "s" : ""}`;
  if (h > 0) return `${h} hour${h > 1 ? "s" : ""}, ${m} min`;
  return `${m} minutes`;
}

function tokens(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(2).replace(/0$/, "") + "M";
  if (n >= 1e3) return Math.round(n / 1e3) + "k";
  return String(n);
}

const modelShort = (m) => m.replace(/^claude-/, "");

// Live data has honest nulls (a bot that never spoke has no last_channel);
// render them as nothing instead of crashing the tab.
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export {
  apiGet, apiSend, MOCK,
  numWord, clockTime, longDate, agoInWords, uptimeInWords, tokens, modelShort, esc,
};
