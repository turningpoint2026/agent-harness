#!/usr/bin/env python3
"""Read-tool ledger. PostToolUse hook on Read.

Appends one JSONL entry per successful Read call to a LOCAL per-session
ledger file. The synthesis coverage gate (synthesis_coverage_guard.py v2)
consults this ledger to verify that corpus files actually entered context
via the Read tool before a synthesis document may be written.

Design rationale (2026-08-06): verify the behavior (ingestion through the
Read tool), not a self-reported artifact. Quote-anchored read logs can be
mechanically harvested with a bash loop without reading anything; a ledger
written by the harness-invoked hook cannot be accrued that way.

Entry: {"path": <normalized abs path>, "full": <bool>}
  full = no offset, and no limit or limit >= 2000 (the Read default).
  Files longer than 2000 lines are counted as fully read by a single
  no-limit Read; pragmatic, documented limitation.

State dir is LOCAL, never cloud-synced (session state must not sync
across machines): ~/.claude/hook_state/
Override for tests: CLAUDE_HOOK_STATE_DIR.

Never blocks, never errors: any failure exits 0 silently. A broken ledger
must never break Reads.
"""

import json
import os
import re
import sys


def norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p))).replace("\\", "/")


def safe(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s))[:80]


def state_dir():
    return os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "hook_state"
    )


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0
    # Log only reads of files that exist (skip errored Reads on missing paths).
    try:
        if not os.path.isfile(file_path):
            return 0
    except OSError:
        return 0

    limit = tool_input.get("limit")
    offset = tool_input.get("offset")
    full = (not offset) and (
        limit is None or (isinstance(limit, (int, float)) and limit >= 2000)
    )

    sid = payload.get("session_id") or "nosession"
    entry = {"path": norm(file_path), "full": bool(full)}
    # Codex integration part 2 (2026-09-07): a full-read claim carries the
    # content hash (Codex acceptance criterion), and a read synthesized from a
    # Codex shell command says so. Hashing is capped so a huge file never stalls
    # the hook; a missing hash is not a partial read.
    if full:
        try:
            cap_mb = float(os.environ.get("CLAUDE_LEDGER_HASH_CAP_MB") or 20)
            if os.path.getsize(file_path) <= cap_mb * 1024 * 1024:
                import hashlib
                h = hashlib.sha256()
                with open(file_path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                entry["sha256"] = h.hexdigest()
        except (OSError, ValueError):
            pass
    if tool_input.get("_codex_shell_read"):
        entry["via"] = "codex-shell"
        if tool_input.get("_codex_partial_reason"):
            entry["partial_reason"] = tool_input["_codex_partial_reason"]
        elif full and "sha256" not in entry:
            # Codex acceptance criterion: no hash, no full-read credit (Claude's Read
            # keeps its documented behaviour for files over the cap).
            entry["full"] = False
            entry["partial_reason"] = "unhashed"
    try:
        os.makedirs(state_dir(), exist_ok=True)
        ledger = os.path.join(state_dir(), f"read_ledger_{safe(sid)}.jsonl")
        with open(ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
