"""
Supervisor daemon entry point (v0.9).

    python supervisor.py [--root <path>] [--port <n>]

Manages bot processes (start/stop/restart, crash recovery) and serves the
dashboard + its API on 127.0.0.1 only. --root names the install it manages
(default: cwd); SLH_SUPERVISOR_PORT overrides the port.
"""

import argparse
import asyncio
import hashlib
import logging
import os
import re
import sys
from pathlib import Path

# Embeddable Python (the bundled installer) skips the implicit script-dir
# sys.path entry; make framework imports location-independent everywhere.
sys.path.insert(0, str(Path(__file__).parent))

from aiohttp import web
from dotenv import load_dotenv

from supervisor.api import build_app
from supervisor.integrations import load_mcp_servers
from supervisor.mcp_health import MCPHealthPoller
from supervisor.paths import SupervisorRoot
from supervisor.process_manager import ProcessManager

logger = logging.getLogger("supervisor")

DEFAULT_PORT = 8642

_ASSET_REF_RE = re.compile(r'(assets/[\w.-]+\.(?:js|css))(?:\?v=[\w.-]+)?')


def _assets_fingerprint(assets_dir: Path) -> str:
    """Hash over every file in assets/ - changes the instant any dashboard
    JS/CSS changes, so cache-busted URLs update themselves instead of
    depending on a hand-maintained ?v=N literal (which already silently
    fell out of sync once). Recomputed per request: cheap at this size,
    and it means editing a JS file takes effect on refresh, no restart."""
    digest = hashlib.sha256()
    for f in sorted(assets_dir.glob("*")):
        if f.is_file():
            digest.update(f.read_bytes())
    return digest.hexdigest()[:10]


def _render_html(path: Path, assets_dir: Path) -> str:
    """Serve an HTML page with every assets/*.js|css reference tagged with
    the live content fingerprint, replacing any ?v=N already in the source."""
    html = path.read_text(encoding="utf-8")
    version = _assets_fingerprint(assets_dir)
    return _ASSET_REF_RE.sub(rf"\1?v={version}", html)


async def main(root_path: Path, port: int, code_root: Path = None) -> None:
    root = SupervisorRoot(root_path, code_root=code_root)
    root.seed()
    # Validation parity needs the managed install's env (file-to-file only;
    # the API never serves these values)
    load_dotenv(root.env_file())

    pm = ProcessManager(root)
    health = MCPHealthPoller(lambda: load_mcp_servers(root))
    stop = asyncio.Event()
    app = build_app(root, pm, health=health, stop_event=stop)

    # Dashboard static files (Plan C lands the real UI here).
    # no-store on everything: Chromium's heuristic caching (keyed off
    # Last-Modified) otherwise keeps serving a STALE bot.html - and with it
    # the old bot.js - after a framework update. Local files, cost is nil.
    # That alone only stops *future* poisoning though - it can't evict a
    # copy already cached under a given URL before no-store existed. The
    # index/bot.html handlers below close that gap for good by tagging
    # every asset reference with a live content hash (_assets_fingerprint),
    # so the URL itself changes whenever the file does - no ?v=N to forget.
    ui_dir = Path(__file__).parent / "supervisor" / "ui"
    if ui_dir.exists():
        @web.middleware
        async def no_cache_ui(request, handler):
            response = await handler(request)
            if not request.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            return response
        app.middlewares.append(no_cache_ui)

        def html_page(filename):
            async def handler(request):
                html = _render_html(ui_dir / filename, ui_dir / "assets")
                return web.Response(text=html, content_type="text/html")
            return handler
        app.router.add_get("/", html_page("index.html"))
        app.router.add_get("/bot.html", html_page("bot.html"))
        app.router.add_static("/", ui_dir)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info(f"Supervisor up: http://127.0.0.1:{port} (root: {root.root})")

    await pm.start_desired()  # bots that were running come back

    watch = asyncio.create_task(pm.watch_loop())
    poll = asyncio.create_task(health.poll_loop())
    try:
        await stop.wait()  # until interrupted or POST /api/supervisor/shutdown
        logger.info("Shutdown requested - stopping bots")
    finally:
        watch.cancel()
        poll.cancel()
        await pm.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discord Agents supervisor")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="data root to manage (default: cwd)")
    parser.add_argument("--code-root", type=Path,
                        default=Path(__file__).parent,
                        help="framework source root (default: beside this script)")
    parser.add_argument("--port", type=int,
                        default=int(os.getenv("SLH_SUPERVISOR_PORT", DEFAULT_PORT)))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        asyncio.run(main(args.root, args.port, code_root=args.code_root))
    except KeyboardInterrupt:
        logger.info("Supervisor shut down")
