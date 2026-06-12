"""
The supervisor's localhost HTTP API. Binds 127.0.0.1 only; the path jail
is the security boundary for every file route; bot YAMLs hold no secrets
and .env is unreachable by construction (outside every jailed root).
"""

import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import yaml
from aiohttp import web

from core import __version__
from core.config import BotConfig

from .data import BotData
from .env_store import EnvStore
from .integrations import (add_skill, apply_skills, load_mcp_servers,
                           remove_skill, save_mcp_servers, skills_catalog)
from .mcp_health import MCPHealthPoller
from .paths import PathJailError, SupervisorRoot
from .process_manager import ProcessManager

logger = logging.getLogger(__name__)

BOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")

DISCORD_API = "https://discord.com/api/v10"
ANTHROPIC_API = "https://api.anthropic.com/v1"

BOT_TEMPLATE = """\
bot_id: {bot_id}
name: "{bot_id}"

discord:
  token_env_var: "{env_var}"
  servers: []

personality:
  base_prompt: |
    You are {bot_id}, a thoughtful resident of this server.

api:
  model: "claude-sonnet-4-6"
"""


def json_response(data, status=200):
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(
        d, ensure_ascii=False, default=str))


def build_app(root: SupervisorRoot, pm: ProcessManager,
              health: MCPHealthPoller = None,
              stop_event: asyncio.Event = None) -> web.Application:
    data = BotData(root)
    env = EnvStore(root.env_file())
    health = health or MCPHealthPoller(lambda: load_mcp_servers(root))
    app = web.Application()
    app["health"] = health
    app["started"] = datetime.now(timezone.utc).isoformat()
    app["inducts"] = {}  # bot_id -> {proc, lines, dry_run, started, done}

    def known(bot_id: str) -> bool:
        return bot_id in root.bot_ids()

    def token_env_var(bot_id: str) -> str:
        """The env var holding this bot's Discord token - from its config,
        with the conventional name as fallback."""
        try:
            cfg = data.load_config(bot_id)
            name = (cfg.get("discord") or {}).get("token_env_var")
            if name:
                return name
        except Exception:
            pass
        return f"{bot_id.upper().replace('-', '_')}_BOT_TOKEN"

    def bot_token(bot_id: str) -> str:
        import os
        var = token_env_var(bot_id)
        return env.get(var) or os.getenv(var, "")

    @web.middleware
    async def errors(request, handler):
        try:
            return await handler(request)
        except PathJailError as e:
            return json_response({"error": str(e)}, status=403)
        except FileNotFoundError as e:
            return json_response({"error": str(e)}, status=404)
        except web.HTTPException:
            raise
        except Exception:
            logger.exception(f"Handler error: {request.path}")
            return json_response({"error": "internal error"}, status=500)

    app.middlewares.append(errors)

    def bot_or_404(request) -> str:
        bot_id = request.match_info["bot_id"]
        if not known(bot_id):
            raise web.HTTPNotFound(text=json.dumps({"error": f"no bot {bot_id}"}),
                                   content_type="application/json")
        return bot_id

    # --- supervisor + fleet -------------------------------------------------

    async def get_supervisor(request):
        bots = await data.list_bots(pm.status)
        tokens = {"uncached_in": 0, "cache_read": 0, "out": 0}
        cost = 0.0
        cost_complete = True
        for b in bots:
            s = await data.status(b["bot_id"], pm.status(b["bot_id"]))
            for k in tokens:
                tokens[k] += s["tokens_today"].get(k, 0)
            if s.get("cost_today_usd") is None:
                if any(s["tokens_today"].values()):
                    cost_complete = False
            else:
                cost += s["cost_today_usd"]
        return json_response({
            "online": True,
            "status": "running",
            "version": __version__,
            "port": request.transport.get_extra_info("sockname")[1]
            if request.transport else None,
            "started": app["started"],
            "bots": len(bots),
            "running": sum(1 for b in bots if b["running"]),
            "tokens_today": tokens,
            "cost_today_usd": round(cost, 4),
            "cost_complete": cost_complete,
            "time": datetime.now(timezone.utc).isoformat(),
        })

    async def get_bots(request):
        return json_response(await data.list_bots(pm.status))

    async def create_bot(request):
        body = await request.json()
        bot_id = (body.get("bot_id") or "").strip()
        if not BOT_ID_RE.match(bot_id):
            return json_response(
                {"error": "bot id must match ^[a-z0-9][a-z0-9-]{1,31}$"}, status=400)
        if known(bot_id):
            return json_response({"error": f"{bot_id} already exists"}, status=409)
        env_var = f"{bot_id.upper().replace('-', '_')}_BOT_TOKEN"
        root.bots_dir().mkdir(parents=True, exist_ok=True)
        root.bot_yaml(bot_id).write_text(
            BOT_TEMPLATE.format(bot_id=bot_id, env_var=env_var), encoding="utf-8")
        logger.info(f"Commissioned bot {bot_id}")
        return json_response({"bot_id": bot_id, "token_env_var": env_var}, status=201)

    async def delete_bot(request):
        bot_id = bot_or_404(request)
        if pm.is_running(bot_id):
            return json_response(
                {"error": f"{bot_id} is running - stop it first"}, status=409)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = root.trash_dir() / f"{bot_id}-{ts}"
        dest.mkdir(parents=True, exist_ok=True)
        moved = []
        candidates = [root.bot_yaml(bot_id), root.memories_dir(bot_id),
                      root.repository_dir(bot_id)]
        candidates += sorted(root.persistence_dir().glob(f"{bot_id}_*"))
        candidates += [root.log_file(bot_id, "main"),
                       root.log_file(bot_id, "conversations")]
        for path in candidates:
            if path.exists():
                shutil.move(str(path), str(dest / path.name))
                moved.append(path.name)
        logger.info(f"Retired bot {bot_id} -> trash/{dest.name}")
        return json_response({"retired": bot_id, "trash": dest.name, "moved": moved})

    # --- lifecycle ------------------------------------------------------------

    async def start_bot(request):
        bot_id = bot_or_404(request)
        return json_response(await pm.start(bot_id))

    async def stop_bot(request):
        bot_id = bot_or_404(request)
        return json_response(await pm.stop(bot_id))

    async def restart_bot(request):
        bot_id = bot_or_404(request)
        return json_response(await pm.restart(bot_id))

    # --- monitor reads -----------------------------------------------------------

    async def get_status(request):
        bot_id = bot_or_404(request)
        return json_response(await data.status(bot_id, pm.status(bot_id)))

    async def get_stats(request):
        bot_id = bot_or_404(request)
        return json_response(await data.stats(bot_id))

    async def get_channels(request):
        bot_id = bot_or_404(request)
        return json_response(await data.channels(bot_id))

    async def get_stream(request):
        bot_id = bot_or_404(request)
        channel = request.query.get("channel")
        if not channel:
            return json_response({"error": "channel parameter required"}, status=400)
        return json_response(await data.stream(
            bot_id, channel,
            limit=min(int(request.query.get("limit", "50")), 200),
            before=request.query.get("before")))

    async def get_logs(request):
        """file=conversations serves the structured trace (events table) in
        the UI's shape; file=main serves parsed log entries; raw=1 falls back
        to plain lines; follow=1 streams (SSE, raw lines)."""
        bot_id = bot_or_404(request)
        which = request.query.get("file", "main")
        tail = min(int(request.query.get("tail", "200")), 2000)
        if request.query.get("follow") == "1":
            return await _follow_log(request, bot_id, which)
        if request.query.get("raw") == "1":
            return json_response({"lines": data.log_tail(bot_id, which, tail)})
        if which == "conversations":
            return json_response(await data.trace(bot_id, tail))
        return json_response(data.main_log_entries(bot_id, tail))

    async def get_episodes(request):
        bot_id = bot_or_404(request)
        return json_response(await data.episodes_list(
            bot_id, min(int(request.query.get("tail", "50")), 500)))

    async def get_skills(request):
        bot_id = bot_or_404(request)
        return json_response(await data.skills_list(
            bot_id, min(int(request.query.get("tail", "50")), 500)))

    async def _follow_log(request, bot_id, which):
        """SSE tail: replay the last lines, then stream appended ones."""
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
        })
        await response.prepare(request)
        path = root.log_file(bot_id, which)
        pos = 0
        if path.exists():
            for line in data.log_tail(bot_id, which, 50):
                await response.write(f"data: {line}\n\n".encode("utf-8"))
            pos = path.stat().st_size
        try:
            while True:
                await asyncio.sleep(1.0)
                if not path.exists():
                    continue
                size = path.stat().st_size
                if size > pos:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                    pos = size
                    for line in chunk.splitlines():
                        await response.write(f"data: {line}\n\n".encode("utf-8"))
                elif size < pos:
                    pos = 0  # rotated
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    # --- config ----------------------------------------------------------------------

    async def get_config(request):
        bot_id = bot_or_404(request)
        return json_response(data.load_config(bot_id))

    async def put_config(request):
        bot_id = bot_or_404(request)
        candidate = await request.json()
        # Validate through the same code the bot boots with
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            yaml.safe_dump(candidate, f, allow_unicode=True, sort_keys=False)
            temp_path = Path(f.name)
        try:
            config = BotConfig.load(temp_path)
            errors = config.validate()
        except Exception as e:
            errors = [f"config did not parse: {e}"]
        finally:
            temp_path.unlink(missing_ok=True)
        if errors:
            return json_response({"errors": errors}, status=400)
        with open(root.bot_yaml(bot_id), "w", encoding="utf-8") as f:
            yaml.safe_dump(candidate, f, allow_unicode=True, sort_keys=False)
        logger.info(f"Config updated for {bot_id}")
        return json_response({"saved": True,
                              "restart_required": pm.is_running(bot_id)})

    # --- trees + files ------------------------------------------------------------------

    async def get_memory_tree(request):
        bot_id = bot_or_404(request)
        return json_response(data.memory_tree(bot_id))

    async def get_memory_file(request):
        bot_id = bot_or_404(request)
        rel = request.query.get("path", "")
        return json_response({"path": rel,
                              "content": data.memory_file(bot_id, rel)})

    async def put_memory_file(request):
        bot_id = bot_or_404(request)
        raw = await request.read()
        try:
            content = json.loads(raw.decode("utf-8")).get("content", "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            content = raw.decode("utf-8")  # plain-text body also accepted
        data.write_memory_file(bot_id, request.query.get("path", ""), content)
        return json_response({"saved": True})

    async def get_repo_tree(request):
        bot_id = bot_or_404(request)
        return json_response(data.repository_tree(bot_id))

    async def get_repo_file(request):
        bot_id = bot_or_404(request)
        rel = request.query.get("path", "")
        blob = data.repository_file(bot_id, rel)
        try:
            return json_response({"path": rel,
                                  "content": blob.decode("utf-8")})
        except UnicodeDecodeError:
            return web.Response(body=blob,
                                content_type="application/octet-stream")

    # --- integrations -------------------------------------------------------------------

    async def get_integrations(request):
        """The PROTOTYPE's shape: flat skills array + mcp_servers array."""
        bot_id = bot_or_404(request)
        config = data.load_config(bot_id)
        catalog = skills_catalog(root, config)
        used = await data.skills_list(bot_id, tail=500)
        week_ago = datetime.now(timezone.utc).date().toordinal() - 7
        skills = [{
            "id": s["name"],
            "name": s["name"],
            "source": s["source"],
            "enabled": s["enabled"],
            "description": s["description"],
            "used_7d": sum(
                1 for u in used if u["name"] == s["name"]
                and datetime.fromisoformat(u["ts"]).date().toordinal() > week_ago),
        } for s in catalog["items"]]
        mcp_servers = []
        for s in load_mcp_servers(root):
            st = health.status_for(s.get("name", "?"))
            mcp_servers.append({
                "name": s.get("name", "?"),
                "transport": s.get("transport", "http"),
                "target": s.get("url", ""),
                "enabled": s.get("enabled", True),
                "status": st["status"],
                "latency_ms": st["latency_ms"],
                "last_check": st["last_check"],
                "tools": st["tools"],
                "error": st.get("error"),
            })
        return json_response({
            "skills": skills,
            "include_anthropic_skills": catalog["include_anthropic_skills"],
            "mcp_servers": mcp_servers,
        })

    async def put_integrations(request):
        """Accepts the UI's whole-object PUT: skills (with toggled enabled)
        + mcp_servers (status fields stripped on save)."""
        bot_id = bot_or_404(request)
        body = await request.json()
        if "skills" in body:
            enabled = [s["name"] for s in body["skills"] if s.get("enabled")]
            apply_skills(root, bot_id, enabled,
                         body.get("include_anthropic_skills", True))
        if "mcp_servers" in body:
            cleaned = [{
                "name": s.get("name"),
                "transport": s.get("transport", "http"),
                "url": s.get("target") or s.get("url", ""),
                "headers": s.get("headers") or {},
                "enabled": s.get("enabled", True),
                "timeout_seconds": s.get("timeout_seconds", 10),
            } for s in body["mcp_servers"]]
            save_mcp_servers(root, cleaned)
            await health.check_all()
        restarted = False
        if pm.is_running(bot_id):
            await pm.restart(bot_id)
            restarted = True
        return json_response({"saved": True, "restarted": restarted})

    async def post_skill(request):
        bot_or_404(request)
        name = request.query.get("name", "")
        blob = await request.read()
        try:
            added = add_skill(root, name, blob)
        except ValueError as e:
            return json_response({"error": str(e)}, status=400)
        return json_response({"added": added}, status=201)

    async def delete_skill(request):
        bot_or_404(request)
        name = request.match_info["name"]
        try:
            removed = remove_skill(root, name)
        except (ValueError, PathJailError) as e:
            return json_response({"error": str(e)}, status=400)
        if not removed:
            return json_response({"error": f"no such skill: {name}"}, status=404)
        return json_response({"removed": name})

    async def reconnect_mcp(request):
        bot_or_404(request)
        name = request.match_info["name"]
        server = next((s for s in load_mcp_servers(root)
                       if s.get("name") == name), None)
        if server is None:
            return json_response({"error": f"no MCP server {name}"}, status=404)
        return json_response(await health.check_server(server))

    # --- secrets (write-only: booleans out, values in, never echoed) -----------

    async def get_setup(request):
        return json_response({
            "anthropic_key_set": env.is_set("ANTHROPIC_API_KEY"),
            "bots": [{"bot_id": b, "token_set": env.is_set(token_env_var(b))}
                     for b in root.bot_ids()],
        })

    async def put_anthropic_key(request):
        key = ((await request.json()).get("key") or "").strip()
        if not key:
            return json_response({"error": "key is required"}, status=400)
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(f"{ANTHROPIC_API}/models", headers={
                        "x-api-key": key, "anthropic-version": "2023-06-01"},
                        timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 401:
                        return json_response(
                            {"error": "Anthropic rejected that key - check it "
                                      "and try again"}, status=400)
                    if r.status != 200:
                        return json_response(
                            {"error": f"could not validate key "
                                      f"(Anthropic returned {r.status})"}, status=502)
            except aiohttp.ClientError as e:
                return json_response(
                    {"error": f"could not reach Anthropic: {e}"}, status=502)
        env.set("ANTHROPIC_API_KEY", key)
        logger.info("Anthropic API key set via UI (validated)")
        return json_response({"saved": True})

    async def put_bot_token(request):
        bot_id = bot_or_404(request)
        token = ((await request.json()).get("token") or "").strip()
        if not token:
            return json_response({"error": "token is required"}, status=400)
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(f"{DISCORD_API}/users/@me", headers={
                        "Authorization": f"Bot {token}"},
                        timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 401:
                        return json_response(
                            {"error": "Discord rejected that token - copy the "
                                      "Bot token from the Developer Portal"},
                            status=400)
                    if r.status != 200:
                        return json_response(
                            {"error": f"could not validate token "
                                      f"(Discord returned {r.status})"}, status=502)
                    me = await r.json()
            except aiohttp.ClientError as e:
                return json_response(
                    {"error": f"could not reach Discord: {e}"}, status=502)
        env.set(token_env_var(bot_id), token)
        logger.info(f"Discord token set for {bot_id} via UI "
                    f"(bot account: {me.get('username')})")
        return json_response({"saved": True,
                              "bot_user": me.get("username"),
                              "bot_user_id": me.get("id"),
                              "restart_required": pm.is_running(bot_id)})

    # --- Discord discovery (which servers is this bot actually in?) ------------

    async def get_guilds(request):
        bot_id = bot_or_404(request)
        token = bot_token(bot_id)
        if not token:
            return json_response(
                {"error": "no Discord token set for this bot yet"}, status=409)
        configured = set(str(s) for s in
                         (data.load_config(bot_id).get("discord") or {})
                         .get("servers", []))
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DISCORD_API}/users/@me/guilds", headers={
                    "Authorization": f"Bot {token}"},
                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return json_response(
                        {"error": f"Discord returned {r.status}"}, status=502)
                guilds = await r.json()
        return json_response([{
            "id": g["id"], "name": g["name"],
            "configured": g["id"] in configured,
        } for g in guilds])

    async def get_guild_channels(request):
        bot_id = bot_or_404(request)
        guild_id = request.match_info["guild_id"]
        if not guild_id.isdigit():
            return json_response({"error": "bad guild id"}, status=400)
        token = bot_token(bot_id)
        if not token:
            return json_response(
                {"error": "no Discord token set for this bot yet"}, status=409)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DISCORD_API}/guilds/{guild_id}/channels",
                             headers={"Authorization": f"Bot {token}"},
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return json_response(
                        {"error": f"Discord returned {r.status}"}, status=502)
                channels = await r.json()
        # text (0), voice (2), announcement (5) - the surfaces the bot lives on
        return json_response(sorted([{
            "id": c["id"], "name": c["name"], "type": c["type"],
        } for c in channels if c.get("type") in (0, 2, 5)],
            key=lambda c: (c["type"], c["name"])))

    # --- repository writes (the bot reconciles disk on its side) ---------------

    async def put_repo_file(request):
        bot_id = bot_or_404(request)
        rel = request.query.get("path", "")
        if not rel:
            return json_response({"error": "path parameter required"}, status=400)
        blob = await request.read()
        p = root.jailed(root.repository_dir(bot_id), rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
        logger.info(f"Repository file saved for {bot_id}: {rel} ({len(blob)} B)")
        return json_response({"saved": True, "path": rel, "size": len(blob)})

    async def delete_repo_file(request):
        bot_id = bot_or_404(request)
        rel = request.query.get("path", "")
        if not rel:
            return json_response({"error": "path parameter required"}, status=400)
        if not data.repo_delete(bot_id, rel):
            return json_response({"error": f"no such entry: {rel}"}, status=404)
        logger.info(f"Repository entry deleted for {bot_id}: {rel}")
        return json_response({"deleted": True, "path": rel})

    async def post_repo_dir(request):
        bot_id = bot_or_404(request)
        body = await request.json()
        rel = str(body.get("path") or "").strip()
        if not rel:
            return json_response({"error": "path required"}, status=400)
        try:
            data.repo_mkdir(bot_id, rel)
        except PathJailError as e:
            return json_response({"error": str(e)}, status=400)
        logger.info(f"Repository folder created for {bot_id}: {rel}")
        return json_response({"created": True, "path": rel})

    async def post_repo_move(request):
        bot_id = bot_or_404(request)
        body = await request.json()
        src = str(body.get("from") or "").strip()
        dst = str(body.get("to") or "").strip()
        if not src or not dst:
            return json_response({"error": "from and to required"}, status=400)
        try:
            data.repo_move(bot_id, src, dst)
        except PathJailError as e:
            return json_response({"error": str(e)}, status=400)
        except (FileNotFoundError, FileExistsError, ValueError) as e:
            return json_response({"error": str(e)}, status=409)
        logger.info(f"Repository move for {bot_id}: {src} -> {dst}")
        return json_response({"moved": True, "from": src, "to": dst})

    # --- induction (memory pre-population from stored backlog) -----------------

    async def _pump_induct(bot_id: str, proc):
        rec = app["inducts"][bot_id]
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                rec["lines"].append(line)
                del rec["lines"][:-400]  # keep a bounded tail
        await proc.wait()
        rec["done"] = True
        rec["returncode"] = proc.returncode

    async def post_induct(request):
        bot_id = bot_or_404(request)
        body = await request.json()
        server = str(body.get("server") or "").strip()
        dry_run = bool(body.get("dry_run", True))
        if not server.isdigit():
            return json_response({"error": "server id required"}, status=400)
        rec = app["inducts"].get(bot_id)
        if rec and not rec["done"]:
            return json_response({"error": "an induction is already running"},
                                 status=409)
        if pm.is_running(bot_id):
            return json_response(
                {"error": "stop the bot first - induction rebuilds its "
                          "starting memory"}, status=409)
        args = [sys.executable, str(root.bot_manager_script()),
                "induct", bot_id, "--server", server]
        if dry_run:
            args.append("--dry-run")
        if body.get("channels"):
            args += ["--channels", ",".join(str(c) for c in body["channels"])]
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(root.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        app["inducts"][bot_id] = {
            "proc": proc, "lines": [], "dry_run": dry_run, "done": False,
            "returncode": None,
            "started": datetime.now(timezone.utc).isoformat(),
        }
        asyncio.create_task(_pump_induct(bot_id, proc))
        logger.info(f"Induction started for {bot_id} (server {server}, "
                    f"dry_run={dry_run})")
        return json_response({"started": True, "dry_run": dry_run}, status=202)

    async def get_induct(request):
        bot_id = bot_or_404(request)
        rec = app["inducts"].get(bot_id)
        if not rec:
            return json_response({"running": False, "lines": []})
        return json_response({
            "running": not rec["done"],
            "dry_run": rec["dry_run"],
            "started": rec["started"],
            "returncode": rec["returncode"],
            "lines": rec["lines"],
        })

    # --- daemon lifecycle -------------------------------------------------------

    async def post_shutdown(request):
        """Graceful stop: every managed bot is stopped (desired state kept),
        then the daemon exits. The app calls this on window close."""
        if stop_event is None:
            return json_response({"error": "shutdown not wired"}, status=501)
        logger.info("Shutdown requested via API")
        asyncio.get_running_loop().call_later(0.1, stop_event.set)
        return json_response({"stopping": True})

    # --- routes ------------------------------------------------------------------------

    app.router.add_get("/api/supervisor", get_supervisor)
    app.router.add_get("/api/bots", get_bots)
    app.router.add_post("/api/bots", create_bot)
    app.router.add_delete("/api/bots/{bot_id}", delete_bot)
    app.router.add_post("/api/bots/{bot_id}/start", start_bot)
    app.router.add_post("/api/bots/{bot_id}/stop", stop_bot)
    app.router.add_post("/api/bots/{bot_id}/restart", restart_bot)
    app.router.add_get("/api/bots/{bot_id}/status", get_status)
    app.router.add_get("/api/bots/{bot_id}/stats", get_stats)
    app.router.add_get("/api/bots/{bot_id}/channels", get_channels)
    app.router.add_get("/api/bots/{bot_id}/stream", get_stream)
    app.router.add_get("/api/bots/{bot_id}/logs", get_logs)
    app.router.add_get("/api/bots/{bot_id}/episodes", get_episodes)
    app.router.add_get("/api/bots/{bot_id}/skills", get_skills)
    app.router.add_get("/api/bots/{bot_id}/config", get_config)
    app.router.add_put("/api/bots/{bot_id}/config", put_config)
    app.router.add_get("/api/bots/{bot_id}/memory/tree", get_memory_tree)
    app.router.add_get("/api/bots/{bot_id}/memory/file", get_memory_file)
    app.router.add_put("/api/bots/{bot_id}/memory/file", put_memory_file)
    app.router.add_get("/api/bots/{bot_id}/repository/tree", get_repo_tree)
    app.router.add_get("/api/bots/{bot_id}/repository/file", get_repo_file)
    app.router.add_get("/api/bots/{bot_id}/integrations", get_integrations)
    app.router.add_put("/api/bots/{bot_id}/integrations", put_integrations)
    app.router.add_post("/api/bots/{bot_id}/skills", post_skill)
    app.router.add_delete("/api/bots/{bot_id}/skills/{name}", delete_skill)
    app.router.add_post("/api/bots/{bot_id}/mcp/{name}/reconnect", reconnect_mcp)
    app.router.add_get("/api/setup", get_setup)
    app.router.add_put("/api/setup/anthropic", put_anthropic_key)
    app.router.add_put("/api/bots/{bot_id}/token", put_bot_token)
    app.router.add_get("/api/bots/{bot_id}/guilds", get_guilds)
    app.router.add_get("/api/bots/{bot_id}/guilds/{guild_id}/channels",
                       get_guild_channels)
    app.router.add_put("/api/bots/{bot_id}/repository/file", put_repo_file)
    app.router.add_delete("/api/bots/{bot_id}/repository/file", delete_repo_file)
    app.router.add_post("/api/bots/{bot_id}/repository/dir", post_repo_dir)
    app.router.add_post("/api/bots/{bot_id}/repository/move", post_repo_move)
    app.router.add_post("/api/bots/{bot_id}/induct", post_induct)
    app.router.add_get("/api/bots/{bot_id}/induct", get_induct)
    app.router.add_post("/api/supervisor/shutdown", post_shutdown)

    return app
