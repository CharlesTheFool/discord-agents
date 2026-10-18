"""Remove 0.13's `model` stamps from saved conversation states.

    python scripts/strip_model_stamps.py persistence/<bot>_conversation_states.db

Run this, with the bot stopped, BEFORE rolling a bot back to a release older
than 0.13.0. Those releases don't know the field and send it to the API,
which refuses the whole request ("Extra inputs are not permitted") in every
channel that has a stamped message. Nothing else in the saved states is
touched; 0.13+ simply treats unstamped messages as the main model's.
"""

import json
import sqlite3
import sys
from pathlib import Path


def strip_model_stamps(db_path: Path) -> int:
    """Strip the stamps in place; returns how many messages changed."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT channel_id, state_json FROM conversation_states").fetchall()
        stripped = 0
        for channel_id, state_json in rows:
            state = json.loads(state_json)
            stamped = [m for m in state.get("messages", []) if "model" in m]
            if not stamped:
                continue
            for message in stamped:
                del message["model"]
            stripped += len(stamped)
            conn.execute("UPDATE conversation_states SET state_json = ? WHERE channel_id = ?",
                         (json.dumps(state), channel_id))
        conn.commit()
        return stripped
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).exists():
        sys.exit(__doc__)
    print(f"Stripped the model stamp from {strip_model_stamps(Path(sys.argv[1]))} message(s).")
