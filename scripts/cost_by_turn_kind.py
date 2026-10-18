"""Where the money goes: API cost grouped by turn kind, from the events table.

    python scripts/cost_by_turn_kind.py <bot>_messages.db --model claude-sonnet-5 [--since 2026-09-01]

Read-only. Every turn event carries the token usage of its model call; this
groups them by kind (mention, scan, proactive, ...) and prices them with the
bot's main model. Two limits keep it from being the whole bill:
  - some model calls never write an event (listed at the end), and
  - before 0.13.1 an event records only the LAST model call of its turn, so
    turns that used tools lose their earlier calls. The table shows how many.
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.internal_constants import estimate_cost_usd  # noqa: E402

TOKEN_FIELDS = ("uncached_in", "cache_read", "cache_write", "out")

# Model calls that write no turn event, so none of their cost appears here.
UNRECORDED_CALLS = (
    "episode distillation (background model)",
    "watch evaluation (background model)",
    "consolidation batches (consolidation model, batch prices)",
    "induction batches (consolidation model, batch prices)",
    "ask_prime judgment calls",
)


def load_events(db_path: Path, since: str):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT kind, payload FROM events WHERE ts >= ? ORDER BY ts", (since,)
        ).fetchall()
    finally:
        conn.close()


def summarize(rows, model: str) -> dict:
    kinds = defaultdict(lambda: {"events": 0, "priced": 0, "tool_turns": 0,
                                 **{f: 0 for f in TOKEN_FIELDS}})
    for kind, payload in rows:
        data = json.loads(payload)
        k = kinds[kind]
        k["events"] += 1
        tokens = data.get("tokens") or {}
        if tokens:
            k["priced"] += 1
            for f in TOKEN_FIELDS:
                k[f] += tokens.get(f, 0) or 0
        if data.get("tool_calls"):
            k["tool_turns"] += 1
    for k in kinds.values():
        k["usd"] = estimate_cost_usd(k, model) or 0.0
    return dict(kinds)


def render(kinds: dict, model: str, since: str) -> str:
    total = sum(k["usd"] for k in kinds.values())
    header = (f"{'kind':<11}{'events':>8}{'w/ tokens':>11}{'tool turns':>12}"
              f"{'uncached in':>13}{'cache read':>13}{'cache write':>13}"
              f"{'out':>11}{'USD':>10}{'share':>8}")
    lines = [f"Cost by turn kind - model {model}, events since {since}", "",
             header, "-" * len(header)]
    for name, k in sorted(kinds.items(), key=lambda kv: -kv[1]["usd"]):
        share = k["usd"] / total if total else 0.0
        lines.append(
            f"{name:<11}{k['events']:>8}{k['priced']:>11}{k['tool_turns']:>12}"
            f"{k['uncached_in']:>13,}{k['cache_read']:>13,}{k['cache_write']:>13,}"
            f"{k['out']:>11,}{k['usd']:>10.2f}{share:>8.0%}")
    lines += ["-" * len(header), f"{'total':<11}{'':>81}{total:>10.2f}", ""]

    unpriced = sorted(n for n, k in kinds.items() if not k["priced"])
    tool_turns = sum(k["tool_turns"] for k in kinds.values())
    lines.append("Coverage:")
    if unpriced:
        lines.append(f"  - kinds with no token data: {', '.join(unpriced)}")
    lines.append(f"  - {tool_turns} turns used tools; each one's earlier model calls"
                 " are missing (an event keeps only its turn's last call),"
                 " so those kinds are undercounted")
    lines.append("  - calls that never write an event, absent from this table:")
    lines += [f"      {c}" for c in UNRECORDED_CALLS]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db", type=Path, help="path to <bot>_messages.db")
    parser.add_argument("--model", required=True,
                        help="the bot's main model, used for prices")
    parser.add_argument("--since", default="0000",
                        help="ISO date; only events at or after it (default: all)")
    args = parser.parse_args(argv)
    if not args.db.exists():
        parser.error(f"no such database: {args.db}")
    kinds = summarize(load_events(args.db, args.since), args.model)
    if not kinds:
        print(f"No events since {args.since}.")
        return 0
    print(render(kinds, args.model, args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
