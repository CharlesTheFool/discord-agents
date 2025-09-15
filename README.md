SLH Bot Workspace

How to run (central runner):
- `python SLH/slh.py`

What’s here:
- `legacy/slh_original.py` — the popular, single‑file bot. Now wired to use modular pieces where available.
- `SLH/` — modular workspace (separate Git repo) with core modules and the runner.

Docs:
- `SLH/docs/ARCHITECTURE.md` — original architecture write‑up
- `docs/LEGACY_SLH_ORIGINAL.md` — legacy code map and hotspots
- `docs/REFACTOR_PLAN.md` — incremental plan to finish the extraction

Env:
- Copy `.env.example` to `.env` and fill in required keys.
