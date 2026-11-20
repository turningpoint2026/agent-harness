#!/usr/bin/env python3
"""Gate bypass listener. UserPromptSubmit hook.

Two hard gates block writes and accept only user authorization as a bypass,
each expressed by a phrase in a prompt:

    override coverage gate   -> synthesis_coverage_guard.py (corpus coverage)
    override claims gate     -> claims_ledger_gate.py (per-claim sourcing)

This hook detects those phrases and drops the matching per-session flag file
the gate checks. They are separate on purpose: authorizing a synthesis
written from a partial corpus says nothing about whether its individual
claims carry sources. Claude cannot trigger UserPromptSubmit hooks, so a
flag written through this path is user-authorized by construction.

Honesty note (2026-08-06 design session): Claude CAN write the flag file
directly via Bash. The gate is friction + legibility, not security — a
forged flag is a deliberate, conspicuous act in the transcript, not the
lazy-path failure the gate exists to interrupt.

Never blocks. Exit 0 always. Prints a confirmation (injected as context)
so the bypass is visible in-session.
"""

import json
import os
import re
import sys


GATES = [
    (
        re.compile(r"override\s+coverage\s+gate", re.IGNORECASE),
        "coverage_bypass",
        "[coverage-gate] User bypass recorded for this session: the synthesis "
        "coverage gate will not block. State the coverage actually achieved in "
        "any deliverable written under this bypass.",
    ),
    (
        re.compile(r"override\s+register\s+gate", re.IGNORECASE),
        "register_bypass",
        "[register-gate] User bypass recorded for this session: the "
        "register-read gate will not block. The mistake registers still "
        "apply as rules; state in the deliverable's close-out which "
        "register(s) went unread.",
    ),
    (
        re.compile(r"override\s+claims?\s+gate", re.IGNORECASE),
        "claims_bypass",
        "[claims-gate] User bypass recorded for this session: the claims gate "
        "will not block. Unsourced claims written under this bypass are still "
        "unsourced — say so in the deliverable rather than letting the silence "
        "read as verification.",
    ),
]


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

    prompt = payload.get("prompt", "") or ""
    hits = [(stem, note) for rx, stem, note in GATES if rx.search(prompt)]
    if not hits:
        return 0

    sid = payload.get("session_id") or "nosession"
    for stem, note in hits:
        try:
            os.makedirs(state_dir(), exist_ok=True)
            flag = os.path.join(state_dir(), f"{stem}_{safe(sid)}.flag")
            with open(flag, "w", encoding="utf-8") as f:
                f.write("user-authorized via prompt phrase\n")
            print(note)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
