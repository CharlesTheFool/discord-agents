# 0.12.1 — Kill phantom attachments (implementation plan)

Self-contained plan, written for an implementing agent with no prior context.
Read CLAUDE.md first (cache layout, testing rules). All work on the `Beta`
branch. Do not release without operator sign-off.

## The bug, from production evidence (2026-06-12, prod log)

The bot announced a gif three separate times in #minecraft-no5 and attached
nothing. Prod was on v0.11.3. The v0.11.3 phantom-attachment guard **fired
correctly both times** (`slh-01.log` 15:02:07 and 15:07:21: "Response claims
an attachment but no files are queued - bouncing back") and **was defeated
both times**: instead of generating the file, the model apologized and
restated the claim, and the one-shot guard let the second claim ship.

Root cause stack:

1. **Poisoned self-history.** The bot's persisted conversation state contains
   its own prior "made the gif / it's attached" messages. The thinking trace
   that knew the truth is gone next turn. The model "remembers" making a gif
   because its own words say so — and it has NO perception of what actually
   got delivered. (It even wrote a wrong self-diagnosis into its memory:
   "the file dies between breaths" — in reality it never ran code execution
   at all in those turns.)
2. **One-shot nudge.** `attachment_nudged` in `_run_tool_loop` allows exactly
   one bounce per turn. A doubled-down second claim ships unchallenged.
3. **A known-false claim can still ship.** After the guard has told the model
   "zero files are queued," there is no mechanism preventing the final text
   from claiming an attachment anyway.

v0.12.0's `send_message` + `attach_outputs` gives ground truth when the model
*uses* it — but compat mode still lets plain final text claim anything.

## Fix 1 — Delivery ground truth in `<recent_messages>`

**File:** `core/reactive_engine.py`, in `_build_volatile_tail` (search for
`<recent_messages>`, currently ~line 989–1015).

Currently the block skips system rows AND the bot's own messages:

```python
if m.is_system or m.author_id == own_id:
    continue
```

Change: include the bot's own messages, marked, with their *actual* delivery
record from Discord (`StoredMessage.has_attachments`, populated from the real
Discord message object — this is ground truth, not the model's claim):

```python
if m.is_system:
    continue
if m.author_id == own_id:
    marker = " [attached a file]" if m.has_attachments else " [no file attached]"
    lines.append(f"{m.message_id} — {m.author_name} (you){marker}: {snippet}")
else:
    lines.append(f"{m.message_id} — {m.author_name}: {snippet}")
```

Bump the fetch from `limit=10` to `limit=15` so own messages don't crowd out
reply targets. Update the block's header line to:

```
(recent channel messages: ids for send_message reply_to targeting, oldest
first; your own messages show what ACTUALLY attached, which may differ from
what they claim)
```

This is the load-bearing fix: when the bot's last message says "made the
gif, it's attached" and the map shows `(you) [no file attached]`, the
contradiction is in front of the model at perception level, every request.

Cache note: this rides the volatile tail (`<context_update>`), which is
downstream of both cache breakpoints — adding lines here is safe and costs
only uncached tokens. Do NOT move any of this into a system block.

## Fix 2 — Escalating nudge, two strikes, then suppression

**File:** `core/reactive_engine.py`, in `_run_tool_loop`. Search for
`attachment_nudged` (~line 1423 init, ~1577–1602 the guard).

Replace the boolean with a counter, allow two bounces, and make the second
one blunt:

```python
attachment_nudges = 0  # phantom-attachment guard, max 2 per turn
```

Guard condition: replace `not attachment_nudged` with
`attachment_nudges < 2`. First-nudge text stays close to current but should
name the v0.12.0 delivery route:

> Your reply mentions an attached file, but no files are queued to send -
> nothing will be attached. Files only exist if created by code execution
> during THIS turn, and the reliable way to deliver one is the send_message
> tool with attach_outputs. Either actually produce the file now, or reword
> your reply so it doesn't claim an attachment.

Second nudge (when `attachment_nudges == 1`):

> Reality check: you have run no code execution this turn, so zero files
> exist, and your reply still claims an attachment. Note that earlier
> messages of yours may also claim attachments that never happened - the
> <recent_messages> map shows what actually attached. Do not describe a file
> as attached. Either generate it now and deliver via send_message
> attach_outputs, or say plainly that you don't have it.

**Suppression after two strikes:** if the guard would fire a third time
(text still matches the attach regex, still no files, still no tool send),
do NOT ship the claim:

- **Periodic path** (`fallback_text is None`): set `result.response_text = ""`
  and break — the bot stays silent. Silence is strictly better than a lie.
- **Must-reply path** (`fallback_text` set): ship the text anyway (a mention
  must get an answer), but `logger.warning` that a still-claiming response
  shipped after two nudges. Don't try to edit the model's text.

Implementation shape: check the regex+no-files condition once; branch on
`attachment_nudges` (0 → nudge 1, 1 → nudge 2, 2 → suppress/ship-with-log).
Keep the existing `loop_iteration < MAX_TOOL_LOOP_ITERATIONS` guard on the
bounces.

## Fix 3 — One doctrine line in the tool description

**File:** `tools/send_message.py`, `SEND_MESSAGE_TOOL["description"]`.

Add one sentence (keep the low-key register of the rest — this is read by
the bot persona, not a coding agent): after the attach_outputs sentence,
append:

> Writing "attached" in your text never attaches anything - files only ride
> a message through attach_outputs or by existing when your turn ends.

## Tests

`tests/` is GITIGNORED (no git recovery — be careful editing there). Add to
`tests/test_send_message_tool.py` or a new `tests/test_phantom_guard.py`:

1. `<recent_messages>` includes own messages with `[no file attached]` /
   `[attached a file]` markers and `(you)` label; others unchanged; system
   rows still skipped. (Build the engine via `ReactiveEngine.__new__`,
   stub `message_memory.get_recent` with `StoredMessage` instances, stub
   `discord_client.user.id`; existing tests in the file show the pattern.)
2. Guard fires twice: simulate two consecutive end_turns whose text matches
   the attach regex with no files — assert two `<system_note>` user messages
   appended, different texts.
3. Third strike, periodic (`fallback_text=None`): response suppressed
   (`response_text == ""`).
4. Third strike, must-reply: text ships, warning logged.

Run: `python -m pytest tests/ -q` — suite must stay green (641+ tests).

## Release

Version → 0.12.1 in `core/__init__.py` + `app/package.json` (the
package.json file starts with a UTF-8 BOM — preserve it; never edit YAMLs or
JSON with PowerShell, see CLAUDE.md). CHANGELOG entry under `## [0.12.1]`.
Commit on Beta, ff-merge to main, push, `git tag -a v0.12.1`,
`gh release create v0.12.1 --prerelease` (no assets — framework-only).
**Get operator sign-off before the release steps.**

## Operational notes for Charles (not code)

- Prod must relaunch the app to pick up 0.12.0/0.12.1 (process running since
  14:58 is on 0.11.3).
- The bot wrote a false self-diagnosis into its memory:
  `memories/slh-01/.../bugs.md` (prod data root) — "the file dies between
  breaths". Delete or correct it, or it keeps reinforcing the folk theory.
