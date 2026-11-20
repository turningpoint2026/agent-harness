#!/usr/bin/env python3
"""Session mode from deterministic signals, plus the correction latch.

UserPromptSubmit hook (harness redesign 2026-09-06, plan steps 2 and 4).
Writes  ~/.claude/state/session_mode_<session_id>.json  with the mode in
force, when it was set, and the verbatim signal that set it. Consumers
(turn_end_gate.py, discuss_mode_gate.py, response_style_contract.py)
import effective_mode() from this file; a missing state file means
"default". Never blocks; any unexpected error exits 0 with no output.

Why: 24 of 25 "keep going" corrections came from campaign sessions and
11 of 11 "discuss first" corrections from paper work (plan finding 2).
The mode is set by the user's own signals, never inferred from the task.

Signals, in precedence order (each one wins over the ones below it):
  1. an explicit  MODE: autopilot|discuss|default  line anywhere in the
     prompt (every handoff paste and the /autopilot skill emit one);
  2. /plan (typed, or as the <command-name>/plan</command-name> echo)
     sets discuss; in the transcript, an EnterPlanMode tool call sets
     discuss and an ExitPlanMode whose result says the plan was approved
     lifts it (a rejected plan stays in discuss);
  3. the replay-tuned phrase lists (stage 1 narrowing, REPLAY_TABLES.md):
     autopilot ON: auto pilot / autopilot / dont stop / keep going /
       keep pushing / push forward / you make the best decision and move forward
     autopilot OFF: "pause" or a MODE: line, NEVER a bare "stop"
       ("stop suggesting end of the day" is an autopilot-session row);
     discuss ON: lets discuss / discuss first / discuss with me / dont rush /
       slow down / jumping too fast  ("just print the instruction" was
       dropped in stage 1: it stamped discuss inside an autopilot session);
     discuss OFF: the go-phrases (go ahead / apply / build it / implement it /
       approved / greenlight) under comms_greenlight's NEGATION_RE guard,
       NEVER a bare "go".
Machine-origin text never changes the mode: comms_greenlight.MACHINE_ORIGIN_RE
plus the count_tags.py MACHINE prefix list (task notifications, system
reminders, slash-command output, Stop-hook feedback) are refused outright.
The one exception is the <command-name>/plan|/autopilot</command-name> echo,
which is the user's own keystroke.

Correction latch (plan step 4): a typed short prompt (<= 900 chars) that
matches one of the correction-family regexes (correction_families.py)
is recorded verbatim (up to 300 chars, family, time) in the same state
file. response_style_contract.py re-injects the newest three for the rest
of the session. No caps on behaviour, no enforcement, never promoted to a
memory (a rule written from one correction caused the next).

Injects one line on every mode change; nothing otherwise.

CLI (used by the /autopilot skill; the session id comes from
CLAUDE_CODE_SESSION_ID, which Claude Code exports to tool shells):
    python session_mode.py --set autopilot [--signal "/autopilot"] [--session SID]
    python session_mode.py --show [--session SID]
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "state")

SHORT_MAX = 900          # build_sources.py SHORT_MAX; the latch reads short typed turns only
QUOTE_MAX = 300          # verbatim quote kept in the state file
LATCH_KEEP = 12          # rows kept on disk; the contract injects the newest 3
MODES = ("autopilot", "discuss", "default")

sys.path.insert(0, HERE)
try:
    from comms_greenlight import MACHINE_ORIGIN_RE, NEGATION_RE  # reused as is
except Exception:  # pragma: no cover - the deployer copies both files together
    MACHINE_ORIGIN_RE = re.compile(
        r"<task-notification>|</task-notification>|<task-id>|<system-reminder>|"
        r"<local-command-stdout>|<command-name>|Per-agent\s+results:", re.I)
    NEGATION_RE = re.compile(
        r"\b(don'?t|do not|not yet|never|hold off|wait|no need|without|stop|"
        r"instead of|rather than|before (you|we|i))\b|别|不要|先不", re.I)
try:
    from correction_families import FAMILIES  # the pinned correction-family regexes
except Exception:  # pragma: no cover
    FAMILIES = {}

# count_tags.py MACHINE prefix list, verbatim (instrument 2 exclusion).
MACHINE = re.compile(
    r"^\s*(Stop hook feedback|<task-notification|\[Request interrupted|<bash-|"
    r"<local-command|<command-name|<system-reminder)", re.I)

MODE_LINE = re.compile(r"^\s*MODE:\s*(autopilot|discuss|default)\b", re.I | re.M)
# The user's own slash keystrokes, as typed or as the transcript echo.
CMD_ECHO = re.compile(r"<command-name>\s*(/plan|/autopilot)\s*</command-name>", re.I)
CMD_TYPED = re.compile(r"^\s*(/plan|/autopilot)\b", re.I)

AUTO_ON = ["auto pilot", "autopilot", "dont stop", "don't stop", "keep going",
           "keep pushing", "push forward", "you make the best decision and move forward"]
AUTO_OFF = ["pause"]
DISC_ON = ["lets discuss", "let's discuss", "discuss first", "discuss with me",
           "dont rush", "don't rush", "slow down", "jumping too fast"]
DISC_OFF = ["go ahead", "apply", "build it", "implement it", "approved",
            "greenlight", "green light"]


def phrase_re(phrases):
    # boundaries per alternative (REGISTER_harness_changes stage 0 candidate)
    return re.compile("|".join(r"\b" + re.escape(p) + r"\b" for p in phrases), re.I)


AUTO_ON_RE, AUTO_OFF_RE = phrase_re(AUTO_ON), phrase_re(AUTO_OFF)
DISC_ON_RE, DISC_OFF_RE = phrase_re(DISC_ON), phrase_re(DISC_OFF)


def guarded(rx, text):
    """First match not preceded (within 60 chars) by a negation, else None."""
    for m in rx.finditer(text):
        if not NEGATION_RE.search(text[max(0, m.start() - 60):m.start()]):
            return m
    return None


def is_machine(text):
    return bool(MACHINE.match(text) or MACHINE_ORIGIN_RE.search(text))


# ---------------------------------------------------------------- state
def state_path(sid):
    return os.path.join(STATE_DIR, "session_mode_%s.json" % (sid or "nosession"))


def load_state(sid):
    try:
        with open(state_path(sid), encoding="utf-8") as fh:
            st = json.load(fh)
        if isinstance(st, dict):
            st.setdefault("mode", "default")
            st.setdefault("corrections", [])
            return st
    except Exception:
        pass
    return {"mode": "default", "since": None, "since_epoch": 0.0, "signal": None,
            "history": [], "corrections": []}


def save_state(sid, st):
    os.makedirs(STATE_DIR, exist_ok=True)
    p = state_path(sid)
    tmp = p + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def set_mode(st, mode, signal, at=None):
    at = at or time.time()
    prev = st.get("mode", "default")
    st["mode"] = mode
    st["since_epoch"] = at
    st["since"] = datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M")
    st["signal"] = signal[:200]
    st.setdefault("history", []).append(
        {"at": st["since"], "from": prev, "to": mode, "signal": signal[:200]})
    st["history"] = st["history"][-20:]
    return prev


# ---------------------------------------------------------------- transcript plan events
APPROVED_RE = re.compile(r"User has approved your plan", re.I)


def _ts_epoch(s):
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def plan_events(transcript_path, after_epoch=0.0):
    """[(epoch, kind, signal)] for EnterPlanMode / ExitPlanMode(approved) after
    after_epoch, in transcript order. Sidechain entries are ignored."""
    events, pending = [], {}
    try:
        fh = open(transcript_path, encoding="utf-8", errors="replace")
    except OSError:
        return events
    with fh:
        for line in fh:
            if "PlanMode" not in line and "approved your plan" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("isSidechain"):
                continue
            msg = d.get("message") or {}
            c = msg.get("content")
            ts = _ts_epoch(d.get("timestamp") or "") or 0.0
            if d.get("type") == "assistant" and isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    if b.get("name") == "EnterPlanMode" and ts > after_epoch:
                        events.append((ts, "discuss", "EnterPlanMode"))
                    elif b.get("name") == "ExitPlanMode":
                        pending[b.get("id")] = ts
            elif d.get("type") == "user" and isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    if b.get("tool_use_id") not in pending:
                        continue
                    cc = b.get("content")
                    if isinstance(cc, list):
                        cc = " ".join(x.get("text", "") for x in cc if isinstance(x, dict))
                    if APPROVED_RE.search(str(cc or "")) and ts > after_epoch:
                        events.append((ts, "default", "ExitPlanMode approved"))
    return events


def effective_mode(sid, transcript_path=None):
    """(mode, since, signal) after folding plan-mode tool events newer than the
    stored stamp. Consumers call this; nothing is written."""
    st = load_state(sid)
    mode, since, signal = st.get("mode", "default"), st.get("since"), st.get("signal")
    if transcript_path and os.path.exists(transcript_path):
        for ts, m, sig in plan_events(transcript_path, st.get("since_epoch") or 0.0):
            if m == "default" and mode != "discuss":
                continue  # an approval lifts discuss only
            mode, signal = m, sig
            since = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    return mode, since, signal


def mode_line(sid, transcript_path=None):
    """The one line response_style_contract injects when a mode is set."""
    mode, since, signal = effective_mode(sid, transcript_path)
    if mode == "default":
        return ""
    t = (since or "")[-5:]
    return '[MODE] %s since %s (from: "%s")' % (mode, t, (signal or "").replace("\n", " ")[:80])


# ---------------------------------------------------------------- prompt handling
def apply_prompt(st, prompt, now=None):
    """Apply one typed prompt to the state. Returns (changed, prev, signal)."""
    now = now or time.time()
    echo = CMD_ECHO.search(prompt)
    if echo:
        cmd = echo.group(1).lower()
        target = "discuss" if cmd == "/plan" else "autopilot"
        if st.get("mode") != target:
            return True, set_mode(st, target, cmd, now), cmd
        return False, st.get("mode"), cmd
    if is_machine(prompt):
        return False, st.get("mode"), None
    ml = MODE_LINE.search(prompt)
    if ml:
        target, sig = ml.group(1).lower(), ml.group(0).strip()
        if st.get("mode") != target:
            return True, set_mode(st, target, sig, now), sig
        return False, target, sig
    typed = CMD_TYPED.match(prompt)
    if typed:
        cmd = typed.group(1).lower()
        target = "discuss" if cmd == "/plan" else "autopilot"
        if st.get("mode") != target:
            return True, set_mode(st, target, cmd, now), cmd
        return False, target, cmd
    mode = st.get("mode", "default")
    if mode == "autopilot":
        m = guarded(AUTO_OFF_RE, prompt)
        if m:
            return True, set_mode(st, "default", m.group(0), now), m.group(0)
    if mode == "discuss":
        m = guarded(DISC_OFF_RE, prompt)
        if m:
            return True, set_mode(st, "default", m.group(0), now), m.group(0)
    m = guarded(AUTO_ON_RE, prompt)
    if m:
        if mode != "autopilot":
            return True, set_mode(st, "autopilot", m.group(0), now), m.group(0)
        return False, mode, m.group(0)
    m = guarded(DISC_ON_RE, prompt)
    if m:
        if mode != "discuss":
            return True, set_mode(st, "discuss", m.group(0), now), m.group(0)
        return False, mode, m.group(0)
    return False, mode, None


def latch_correction(st, prompt, now=None):
    """Record a typed short prompt that matches a BASELINES.md family."""
    if not FAMILIES or len(prompt) > SHORT_MAX or is_machine(prompt):
        return None
    if MODE_LINE.search(prompt):
        return None  # a handoff paste is an instruction, not a correction
    for fam, pat in FAMILIES.items():
        if re.search(pat, prompt, re.I):
            now = now or time.time()
            row = {"at": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
                   "family": fam, "quote": prompt.strip()[:QUOTE_MAX]}
            rows = st.setdefault("corrections", [])
            if rows and rows[-1].get("quote") == row["quote"]:
                return None
            rows.append(row)
            st["corrections"] = rows[-LATCH_KEEP:]
            return row
    return None


def fold_plan_events(st, transcript_path, now=None):
    """Persist EnterPlanMode / ExitPlanMode(approved) events newer than the stamp."""
    changed = False
    if not transcript_path or not os.path.exists(transcript_path):
        return changed
    for ts, m, sig in plan_events(transcript_path, st.get("since_epoch") or 0.0):
        if m == "default" and st.get("mode") != "discuss":
            st["since_epoch"] = max(st.get("since_epoch") or 0.0, ts)
            continue
        if st.get("mode") != m:
            set_mode(st, m, sig, ts)
            changed = True
        else:
            st["since_epoch"] = max(st.get("since_epoch") or 0.0, ts)
    return changed


def main_hook():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    data = json.loads(sys.stdin.read() or "{}")
    prompt = data.get("prompt") or ""
    sid = data.get("session_id") or ""
    if not prompt:
        return
    st = load_state(sid)
    before = st.get("mode", "default")
    lines = []
    folded = fold_plan_events(st, data.get("transcript_path"))
    if folded:
        lines.append('[MODE CHANGE] %s -> %s (from: "%s")' % (before, st["mode"], st["signal"]))
    changed, prev, signal = apply_prompt(st, prompt)
    if changed:
        lines.append('[MODE CHANGE] %s -> %s since %s (from: "%s")'
                     % (prev, st["mode"], st["since"][-5:], (signal or "").replace("\n", " ")[:80]))
    latched = latch_correction(st, prompt)
    if folded or changed or latched:
        save_state(sid, st)
    if lines:
        print("\n".join(lines))


def main_cli(argv):
    sid = None
    if "--session" in argv:
        sid = argv[argv.index("--session") + 1]
    sid = sid or os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if "--show" in argv:
        mode, since, signal = effective_mode(sid or "nosession")
        print("session %s: mode %s since %s (from: %s)" % ((sid or "?")[:8], mode, since, signal))
        return 0
    if "--set" in argv:
        mode = argv[argv.index("--set") + 1].lower()
        if mode not in MODES:
            print("mode must be one of " + ", ".join(MODES), file=sys.stderr)
            return 1
        if not sid:
            print("no session id: pass --session SID or run inside a Claude Code tool shell "
                  "(CLAUDE_CODE_SESSION_ID)", file=sys.stderr)
            return 1
        signal = argv[argv.index("--signal") + 1] if "--signal" in argv else "MODE: " + mode
        st = load_state(sid)
        prev = set_mode(st, mode, signal)
        save_state(sid, st)
        print('[MODE CHANGE] %s -> %s since %s (from: "%s") -> %s'
              % (prev, mode, st["since"][-5:], signal, state_path(sid)))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
        sys.exit(main_cli(sys.argv[1:]))
    try:
        main_hook()
    except Exception:
        pass
    sys.exit(0)
