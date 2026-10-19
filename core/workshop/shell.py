"""The shell wrapper every workshop command runs inside (spec 5.7).

Commands are stateless: the wrapper restores the executor's cwd and exported
variables from /workspace/.state/{key}.env, runs the command with ALL output
going to a log file in the workspace (msb would otherwise keep detached
output on the host, uncapped - Phase 0 V6), and saves the state back on any
exit, `exit N` included. It saves only exports that differ from the exec's
own starting env, and never the proxy variables, which every exec brings
fresh (a rotated credential takes effect on the next command).

The wrapper is passed inline (`bash -c WRAPPER da-run KEY LOG CMD`), so
nothing is installed in a sandbox and changing it never rebuilds a template.
"""
from typing import List, Tuple

from core.internal_constants import WORKSHOP_TOOL_OUTPUT_CHARS

WRAPPER = r"""
K="$1"; LOG="$2"; CMD="$3"
S="/workspace/.state/$K.env"
mkdir -p /workspace/.state "$(dirname "$LOG")"
BASE="$(export -p)"
SKIP=' (HTTPS?_PROXY|https?_proxy|NODE_USE_ENV_PROXY|PWD|OLDPWD|SHLVL|_)='
save() {
  rc=$?
  { printf 'cd %q\n' "$PWD"
    comm -13 <(printf '%s\n' "$BASE" | sort) <(export -p | sort) | grep -v -E "$SKIP"
  } > "$S.tmp" 2>/dev/null && mv -f "$S.tmp" "$S"
  exit $rc
}
cd /workspace
[ -f "$S" ] && source "$S" > /dev/null 2>&1
trap save EXIT
exec > "$LOG" 2>&1 < /dev/null
eval "$CMD"
"""

_SPLIT = "<<<da-tail>>>"
_HALF = WORKSHOP_TOOL_OUTPUT_CHARS // 2


def command_argv(executor_key: str, log_path: str, command: str) -> List[str]:
    return ["bash", "-c", WRAPPER, "da-run", executor_key, log_path, command]


def preview_argv(log_path: str, preview_path: str) -> List[str]:
    """Size, head and tail of a log, without pulling a huge log off the VM.
    Written to the root filesystem: with /workspace full the log itself may
    be empty, and that is worth saying."""
    script = (f'f="$1"; [ -f "$f" ] || {{ echo 0; echo "[the output log is gone]"; exit 0; }}; '
              f'n=$(wc -c < "$f" 2>/dev/null || echo 0); echo "$n"; '
              f'avail=$(df -Pk /workspace 2>/dev/null | awk "NR==2 {{print \\$4}}"); '
              f'[ "${{avail:-1024}}" -lt 1024 ] && echo "[/workspace is full - output may be missing]"; '
              f'if [ "$n" -le {WORKSHOP_TOOL_OUTPUT_CHARS} ]; then cat "$f" 2>/dev/null; '
              f'else head -c {_HALF} "$f"; printf "\\n{_SPLIT}\\n"; tail -c {_HALF} "$f"; fi')
    return ["bash", "-c", f'({script}) > "$2" 2>&1', "da-preview", log_path, preview_path]


def parse_preview(raw: bytes) -> Tuple[int, str, bool]:
    """(total bytes, text, truncated) from preview_argv's output."""
    text = raw.decode("utf-8", errors="replace")
    first, _, rest = text.partition("\n")
    if not first.strip().isdigit():   # the size line went missing: show what came back
        return 0, text, False
    total = int(first.strip())
    truncated = _SPLIT in rest
    if truncated:
        head, _, tail = rest.partition(f"\n{_SPLIT}\n")
        rest = f"{head}\n\n[... {total - 2 * _HALF:,} bytes omitted ...]\n\n{tail}"
    return total, rest, truncated


def format_result(exit_code: int, preview: bytes, log_path: str, *, timed_out: bool = False) -> str:
    total, text, truncated = parse_preview(preview)
    status = "stopped at its time limit" if timed_out else f"exit code {exit_code}"
    out = f"[{status}]\n{text.rstrip()}" if text.strip() else f"[{status}, no output]"
    if truncated:
        out += f"\n[full output ({total:,} bytes) in {log_path}]"
    return out
