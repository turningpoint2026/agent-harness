#!/usr/bin/env python3
"""Discuss-mode gate. PreToolUse on Write|Edit|MultiEdit|NotebookEdit|Agent|Task|Workflow
(harness redesign 2026-09-06, plan step 3).

In discuss mode (set by session_mode.py from the user's own signals: a
MODE: discuss line, /plan, EnterPlanMode, "lets discuss", "dont rush", ...)
this hook blocks every write outside the scratchpad and every worker
spawn, on every attempt, because the remedy is to talk, not to retry.
Built-in plan mode already blocks edits; this is the backstop for
sessions that were told to discuss but never entered plan mode
(tagged corrections: "you are jumping to execution too fast",
"dont rush to implementation, lets discuss here first").

Exempt (plan section 3.4, debate t1:d2:p6): paths under _loop_work\\,
SESSION_STATE* and Claude_ProjectMemory* files, handoff files (basename
carrying HANDOFF or RESUME), register appends (files under a registers\\
folder, REGISTER_*.md, LESSONS_CAMPAIGN.md), the
scratchpad, and everything under ~\\.claude (plan files, state, memory).

Lifted by: MODE: default|autopilot, ExitPlanMode with an approved plan,
or one of the go-phrases (go ahead / apply / build it / implement it /
approved / greenlight) typed by the user, negation-guarded; never a bare "go".

Fails open: any unexpected error exits 0.
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from session_mode import effective_mode, DISC_OFF
except Exception:  # pragma: no cover
    def effective_mode(sid, transcript_path=None):
        return "default", None, None
    DISC_OFF = ["go ahead", "apply", "build it", "implement it", "approved", "greenlight"]

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SPAWN_TOOLS = {"Agent", "Task", "Workflow"}

EXEMPT_PARTS = (
    os.sep + "_loop_work" + os.sep,
    os.sep + "scratchpad" + os.sep,
    os.sep + ".claude" + os.sep,
    os.sep + "registers" + os.sep,
    os.sep + "Temp" + os.sep + "claude" + os.sep,
)
EXEMPT_BASENAME = re.compile(
    r"^(session_state|claude_projectmemory|register_)|handoff|resume|"
    r"^lessons_campaign\.md$", re.I)


def exempt(path):
    ap = os.path.abspath(path)
    low = ap.lower()
    if any(part.lower() in low for part in EXEMPT_PARTS):
        return True
    return bool(EXEMPT_BASENAME.search(os.path.basename(ap)))


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    tool = payload.get("tool_name") or ""
    if tool not in WRITE_TOOLS and tool not in SPAWN_TOOLS:
        return 0
    sid = payload.get("session_id", "nosession")
    mode, since, signal = effective_mode(sid, payload.get("transcript_path"))
    if mode != "discuss":
        return 0
    target = ""
    if tool in WRITE_TOOLS:
        target = (payload.get("tool_input") or {}).get("file_path") or (
            payload.get("tool_input") or {}).get("notebook_path") or ""
        if not target or exempt(target):
            return 0
        what = "%s to %s" % (tool, target)
    else:
        what = "%s spawn" % tool
    sys.stderr.write(
        "[DISCUSS MODE] blocked %s.\n"
        "Discuss mode since %s (signal: \"%s\"). Nothing is written outside the scratchpad "
        "and no workers are spawned until the user lifts it: they type one of %s, "
        "approves the plan (ExitPlanMode), or pastes MODE: default or MODE: autopilot. "
        "Bare 'go' does not lift it. Exempt: _loop_work\\, SESSION_STATE*, "
        "Claude_ProjectMemory*, handoff files, register appends, the scratchpad. "
        "Blocks on every attempt; the remedy is to discuss in the reply.\n"
        % (what, since or "?", (signal or "?").replace("\n", " ")[:80],
           " / ".join(DISC_OFF)))
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
