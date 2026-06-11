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
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from aiohttp import web

from core.config import BotConfig

from .data import BotData
from .paths import PathJailError, SupervisorRoot
from .process_manager import ProcessManager

logger = logging.getLogger(__name__)

BOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")

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


def build_app(root: SupervisorRoot, pm: ProcessManager) -> web.Application:
    data = BotData(root)
    app = web.Application()

    def known(bot_id: str) -> bool:
        return bot_id in root.bot_ids()

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
        for b in bots:
            s = await data.status(b["bot_id"], pm.status(b["bot_id"]))
            for k in tokens:
                tokens[k] += s["tokens_today"].get(k, 0)
        return json_response({
            "status": "running",
            "bots": len(bots),
            "running": sum(1 for b in bots if b["running"]),
            "tokens_today": tokens,
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
        bot_id = bot_or_404(request)
        which = request.query.get("file", "main")
        tail = min(int(request.query.get("tail", "200")), 2000)
        if request.query.get("follow") == "1":
            return await _follow_log(request, bot_id, which)
        return json_response({"lines": data.log_tail(bot_id, which, tail)})

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
        return web.Response(text=data.memory_file(
            bot_id, request.query.get("path", "")), content_type="text/plain")

    async def put_memory_file(request):
        bot_id = bot_or_404(request)
        content = (await request.read()).decode("utf-8")
        data.write_memory_file(bot_id, request.query.get("path", ""), content)
        return json_response({"saved": True})

    async def get_repo_tree(request):
        bot_id = bot_or_404(request)
        return json_response(data.repository_tree(bot_id))

    async def get_repo_file(request):
        bot_id = bot_or_404(request)
        blob = data.repository_file(bot_id, request.query.get("path", ""))
        return web.Response(body=blob, content_type="application/octet-stream")

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
    app.router.add_get("/api/bots/{bot_id}/config", get_config)
    app.router.add_put("/api/bots/{bot_id}/config", put_config)
    app.router.add_get("/api/bots/{bot_id}/memory/tree", get_memory_tree)
    app.router.add_get("/api/bots/{bot_id}/memory/file", get_memory_file)
    app.router.add_put("/api/bots/{bot_id}/memory/file", put_memory_file)
    app.router.add_get("/api/bots/{bot_id}/repository/tree", get_repo_tree)
    app.router.add_get("/api/bots/{bot_id}/repository/file", get_repo_file)

    return app
