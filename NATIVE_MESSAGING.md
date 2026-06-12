# Native messaging (0.12.0) — design

The model stops fitting its entire turn into one captured final text and
talks to Discord through a `send_message` tool. Sending becomes a deliberate,
observable act; staying silent becomes simply not sending.

## Why

Three 0.11.x field bugs share one root cause — the model has no ground truth
about its own I/O:

- **Phantom attachments**: it wrote "full figure attached", nothing was
  attached, and next turn it trusted its own claim (the thinking trace that
  knew better is gone by then). 0.11.3 added a regex bounce-back; that is a
  heuristic backstop, not a fix.
- **No reply targeting**: when a periodic batch answers one specific message,
  there is no way to anchor the answer to it.
- **Fragmentation by decree**: texting-style message splitting is enforced
  mechanically after the fact (0.11.3), because the model has no concept of
  "a message" as a unit it controls.

With tool-based send, each call is one Discord message with explicit
parameters. A file is either in the call or it isn't. The tool result tells
the model exactly what went out (message id, attached files) — confabulation
becomes mechanically impossible to sustain.

## Phase 1: compat mode (this release)

The tool exists alongside the legacy capture-final-text path. Plain final
text still sends as before — zero behavior change for turns that don't use
the tool. This de-risks the migration: prompting nudges the model toward the
tool, telemetry shows adoption, nothing breaks if it ignores it.

**Tool**: `send_message(content, reply_to_message_id?, attach_outputs?)`
- `content` — one message, texting-sized; fragment_message still applies as
  a mechanical guarantee (no blank lines in any sent message).
- `reply_to_message_id` — anchors a Discord reply. Invalid/deleted target →
  sends standalone, tool result says so.
- `attach_outputs` — filenames from this turn's code-execution outputs.
  Resolution is by filename against collected container file ids; misses are
  reported in the tool result ("not found among this turn's outputs"), which
  is the ground-truth feedback the phantom-attachment bug demanded.

**Message-id exposure**: the model can't target what it can't name. The
volatile tail (`<context_update>`) gains a `<recent_messages>` map — the
last ~10 human messages' ids, authors, and snippets, queried from
message_memory. Volatile by design (never cached, never persisted), costs
~200 uncached tokens per request.

**Execution seam**: a branch in `_execute_tool_blocks`, placed BEFORE the
MCP `"_" in name` fallthrough (same constraint as ask_prime — the name
contains an underscore). Sends go to the current channel only; vault
boundaries are untouched because nothing crosses a channel.

**ToolLoopResult** carries `sent_message_ids`, `last_sent_message` (for
engagement tracking), and `consumed_file_ids` (tool-attached files don't
re-ride the final text).

**Pipeline semantics**:
- Must-reply (`fallback_text`): satisfied by ≥1 tool send OR final text.
  The "tools ran but quiet" reprompt and the fallback only fire when neither
  happened.
- Periodic silence: empty text AND no sends = stayed silent.
- Staleness guard (periodic): a turn that already tool-sent messages is
  never discarded as stale — they're out. The guard only protects the
  unsent final text.
- Engagement tracking: tracks the last message that went out, whether
  tool-sent or text-sent. Rate limiting still counts one response per turn.
- Persistence: tool_use/tool_result blocks already persist through
  `add_tool_use_and_results` — the transcript shows every send. Final-text
  persistence unchanged. An all-tool turn persists an assistant turn only if
  there's thinking or text (no empty content blocks).

## Phase 2: strict mode (later, separate discussion)

- Final text stops auto-sending (becomes internal scratch / drops).
- `fallback_text` semantics replaced by "must call send_message" enforcement.
- Agentic/proactive paths move onto the tool.
- Per-message pacing (typing indicator between sends, natural delays).
- Revisit `<recent_messages>` cost vs persisting ids in state.

Phase 2 starts only after phase 1 has soaked in the field and we've watched
how the model actually uses the tool.

## Operator decisions (settled 2026-06-12)

1. **Prompting**: frame the tool as the model's *primary messaging
   interface*, not just another tool, and explicitly teach multi-call
   chaining — several sends per turn, guaranteed ordering, e.g. an anchored
   reply to one person plus a separate general message for the rest. The
   tool description carries the full picture; the scan criteria add two
   light lines. Ordering is structural (tool blocks execute sequentially);
   pacing matches the legacy fragment path (back-to-back).
2. **Staleness bypass**: accepted as-is for phase 1. Add a "re-read before
   sending" tail warning only if a stale tool-send is actually observed in
   the field.
3. **Phase 2 timing**: no longitudinal gate — the project is too small for
   slow soak. Validate phase 1 live on the test server (dev build), ship,
   and accept minor behavioral side effects; the architecture move is the
   point.
