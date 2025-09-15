Refactor Plan: From Monolith To Modules

Goal: Gradually replace `legacy/slh_original.py` with a maintainable package while keeping production stable. No big‑bang rewrite.

Phases

1) Extract pure utilities
- Move `Config`, `StateStore`, `APIThrottler`, `SimpleRateLimiter` into a small `slh/core/` package.
- Keep imports in the legacy file pointed at new modules to avoid behavior changes.

2) Extract processors
- Move image and text file processing into `slh/processors/image.py` and `slh/processors/text.py`.
- Keep signatures the same and cover edge cases (size/encoding) with unit tests.

3) Separate conversation logic
- Create `slh/agent/` with:
  - `conversation.py` (analyze_conversation_chunk)
  - `web_search.py` (analyze_with_web_search)
  - `provocation.py` (_find_engagement_target, _generate_provocation)
  - `execution.py` (execute_response_plan, engagement tracking)

4) Event layer and tasks
- Build a thin `slh/discord/` layer that wires discord.py events to the agent functions.
- Move scheduled tasks (periodic check, provocations) to `slh/tasks/` and inject dependencies.

5) Entry points
- Add a small `bin/run_legacy_compat.py` that instantiates the modular bot but preserves env and behavior.
- Keep `legacy/slh_original.py` as a fallback until confidence is high; then retire it to `legacy/ARCHIVE/`.

6) Tests and CI
- Add unit tests for processors and agent decisions using synthetic message objects.
- Add a minimal workflow (lint + unit tests) to keep regressions down.

Guiding Principles

- Behavior parity first: use “facade” functions so the legacy bot still calls into new modules.
- Small PRs: a few functions at a time to keep diffs readable.
- Dependency injection over globals to make testing cheap.

Suggested Module Layout

- `slh/core/` — config, state, throttling, rate limiter
- `slh/agent/` — conversation analysis, web search, provocations, response execution
- `slh/processors/` — image and text processors
- `slh/discord/` — event glue to discord.py
- `slh/tasks/` — periodic analyzers and schedules

Migration Checklist

- Keep `docs/LEGACY_SLH_ORIGINAL.md` accurate after each extraction (update file references).
- Add quick unit tests as you extract to fix regressions early.
- Roll out behind a feature flag, e.g. `USE_MODULAR=true`.

