"""
Supervisor daemon entry point (v0.9).

    python supervisor.py [--root <path>] [--port <n>]

Manages bot processes (start/stop/restart, crash recovery) and serves the
dashboard + its API on 127.0.0.1 only. --root names the install it manages
(default: cwd); SLH_SUPERVISOR_PORT overrides the port.
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

from supervisor.api import build_app
from supervisor.integrations import load_mcp_servers
from supervisor.mcp_health import MCPHealthPoller
from supervisor.paths import SupervisorRoot
from supervisor.process_manager import ProcessManager

logger = logging.getLogger("supervisor")

DEFAULT_PORT = 8642


async def main(root_path: Path, port: int) -> None:
    root = SupervisorRoot(root_path)
    # Validation parity needs the managed install's env (file-to-file only;
    # the API never serves these values)
    load_dotenv(root.root / ".env")

    pm = ProcessManager(root)
    health = MCPHealthPoller(lambda: load_mcp_servers(root))
    app = build_app(root, pm, health=health)

    # Dashboard static files (Plan C lands the real UI here)
    ui_dir = Path(__file__).parent / "supervisor" / "ui"
    if ui_dir.exists():
        async def index(request):
            return web.FileResponse(ui_dir / "index.html")
        app.router.add_get("/", index)
        app.router.add_static("/", ui_dir)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info(f"Supervisor up: http://127.0.0.1:{port} (root: {root.root})")

    watch = asyncio.create_task(pm.watch_loop())
    poll = asyncio.create_task(health.poll_loop())
    try:
        await asyncio.Event().wait()  # run until interrupted
    finally:
        watch.cancel()
        poll.cancel()
        await pm.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discord Agents supervisor")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="install to manage (default: cwd)")
    parser.add_argument("--port", type=int,
                        default=int(os.getenv("SLH_SUPERVISOR_PORT", DEFAULT_PORT)))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        asyncio.run(main(args.root, args.port))
    except KeyboardInterrupt:
        logger.info("Supervisor shut down")
