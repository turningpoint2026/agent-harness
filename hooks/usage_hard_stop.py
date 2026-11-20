#!/usr/bin/env python3
"""
usage_hard_stop.py - PreToolUse hard budget gate on per-session token usage.

Origin: 2026-08-28, a video-coding session - two workflow lanes burned ~36M cache-write
tokens (over half a 5h MAX-20x window) in under 2h, 97% of it one second-reader lane
re-reading 925 native frames. Nothing in the harness would ever have stopped it.

What it does, on every matched tool call (spawn tools + Read + Bash):
  1. Incrementally scans THIS session's transcript + all its subagent/workflow
     transcripts (offsets cached, so warm calls read only new bytes).
  2. Sums exact per-message usage (input, output, cache-write, cache-read),
     deduped by message id (streamed chunks repeat the same usage block).
  3. Weighted total = in + 1.25*cache_write + 0.1*cache_read + 5*out
     (Opus API price ratios - a billed-cost proxy, not a quota number).
  4. >= warn_frac of budget: injects a systemMessage warning with the numbers.
     > budget while armed: BLOCKS the call (exit 2) until the user raises the
     budget or disarms.

Config (user-editable, created on first run):
  ~/.claude/state/usage_budget.json
    {"armed": true, "weighted_budget_M": 25.0, "warn_frac": 0.8}
Bypass is USER-ONLY: edit that file ("armed": false or raise weighted_budget_M).
Claude must not edit it on its own judgment - the whole point is that the burning
session cannot lift its own gate.

Campaign state hand-off (harness redesign step 5, 2026-09-06, ruling D5):
  Optional config key "campaign_state_path": the CAMPAIGN_STATE.json the
  external watchdog reads. When present, and ONLY for a session whose cwd
  lies inside that file's folder (an unrelated session hitting its own budget
  must never rotate the campaign), this hook writes into that file:
    mode ROTATE_PENDING  on the warn_frac warning, if the mode is RUNNING;
    mode HALTED_BUDGET   on a hard block, if the mode is RUNNING or
                         ROTATE_PENDING;
  plus a mode_set_by {hook, session, time, reason} record and updated_utc,
  every other key untouched. PAUSED_*/HALTED_* set by a human are never
  overwritten. Key absent -> nothing is written anywhere but the meter.
  The watchdog relaunches out of ROTATE_PENDING once the session has exited
  and never out of HALTED_BUDGET (finding 6: before this, a budget stop told
  the watchdog nothing, so every restart was the owner's).

State: ~/.claude/state/usage_meter/<session_id>.json (offsets,
per-file totals, dedup window). Timeline: usage_meter/log.csv for tuning.

Delivery and the handoff exemption (2026-09-24, /insights fixes):
  The warning goes out as systemMessage (for the user) AND as PreToolUse
  additionalContext (for the model). systemMessage alone never reached the
  model: in 23 sessions that got it, the next six replies almost never
  mentioned the budget; a `claude -p` probe on 2.1.281 quoted an
  additionalContext token back. Over budget, a Read of a handoff or state file
  (HANDOFF_EXEMPT_RE on the basename) still passes, so a stopped session can
  verify the handoff it wrote; Write/Edit are not matched by this gate at all.

Per-subagent cap (2026-11-07, approved by the owner as "limit each helper
inside a session to 8M"):
  Origin: a token-burn audit of an image-coding campaign (2026-11-07):
  single sub-roles burned up to 51.6M weighted in an image re-read loop while
  siblings doing the same job used about 2-4M; the session-level gate cannot
  stop one runaway helper without stopping the whole session.
  Identification (probed 2026-11-07, Claude Code 2.1.319): a PreToolUse payload
  fired inside a subagent carries "agent_id" and "agent_type"; the main
  thread's payload carries neither. transcript_path is the PARENT session's
  transcript in both cases; the subagent's own transcript is
  <session dir>/<session_id>/subagents/agent-<agent_id>.jsonl.
  Rule: when agent_id is present, the weighted total of that one file (same
  formula, same per-file scan state as the session sum) is compared with
  "subagent_budget_M" (default 8; 0 or negative disables; "armed": false
  disables both gates). Past warn_frac: additionalContext warning to that
  subagent (cooldown per agent; no systemMessage, the user does not need it).
  Over the cap: exit 2 with an order to write results to disk and return.
  The parent session and the other subagents are unaffected; the session
  gate runs first and unchanged. No campaign-state write for a subagent cap.
  Events: usage_meter/subagent_events.csv (warn / block rows).

Fail-open: any unexpected error exits 0. Scan hard-capped at ~25s.
"""
import json
import os
import re
import sys
import time
import glob

HOME = os.path.expanduser("~")
# CLAUDE_HOOK_STATE_DIR is the test harness override (test_hooks.py); live
# sessions never set it, so state stays under ~/.claude/state.
STATE_DIR = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(HOME, ".claude", "state")
METER_DIR = os.path.join(STATE_DIR, "usage_meter")
CONFIG_PATH = os.path.join(STATE_DIR, "usage_budget.json")

DEFAULT_CONFIG = {"armed": True, "weighted_budget_M": 25.0, "warn_frac": 0.8,
                  "subagent_budget_M": 8.0}
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
WEIGHTS = {"in": 1.0, "cw": 1.25, "cr": 0.1, "out": 5.0}
DEDUP_WINDOW = 80          # message ids kept per file (streamed dupes are adjacent)
SCAN_DEADLINE_S = 25.0     # fail open past this
WARN_COOLDOWN_S = 300      # do not re-warn more often than this
# Handoff and state files a stopped session may still Read to verify its close-out.
HANDOFF_EXEMPT_RE = re.compile(
    r"(HANDOFF|RESUME|SESSION_STATE|Claude_ProjectMemory|CAMPAIGN_STATE)", re.IGNORECASE)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def atomic_write(path, obj):
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


ROTATE_ALLOWED_FROM = ("RUNNING",)
HALT_ALLOWED_FROM = ("RUNNING", "ROTATE_PENDING")


def _inside(path, folder):
    try:
        a = os.path.normcase(os.path.abspath(path))
        b = os.path.normcase(os.path.abspath(folder))
        return a == b or a.startswith(b.rstrip("\\/") + os.sep)
    except Exception:
        return False


def campaign_state_write(cfg, hook, new_mode, reason):
    """Write new_mode into the registered campaign state file, or do nothing.

    Nothing when: the key is absent, the file is missing or unreadable, the
    session's cwd is outside the campaign folder, or the current mode is not
    one this transition may overwrite. Returns the mode written, else None.
    """
    path = cfg.get("campaign_state_path")
    if not path:
        return None
    path = os.path.expandvars(os.path.expanduser(str(path)))
    if not os.path.isfile(path):
        return None
    cwd = hook.get("cwd") or ""
    if not cwd or not _inside(cwd, os.path.dirname(path)):
        return None
    data = load_json(path, None)
    if not isinstance(data, dict):
        return None
    cur = data.get("mode")
    allowed = ROTATE_ALLOWED_FROM if new_mode == "ROTATE_PENDING" else HALT_ALLOWED_FROM
    if cur not in allowed:
        return None
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["mode"] = new_mode
    data["mode_set_by"] = {"hook": "usage_hard_stop.py",
                           "session": (hook.get("session_id") or "")[:8],
                           "time": now, "reason": reason, "previous_mode": cur}
    data["updated_utc"] = now
    # Same shape the campaign writes (indent 1, UTF-8 unescaped), so a diff of
    # the state file shows the three changed keys and nothing else.
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return new_mode


def usage_tuple(u):
    return [
        int(u.get("input_tokens") or 0),
        int(u.get("output_tokens") or 0),
        int(u.get("cache_read_input_tokens") or 0),
        int(u.get("cache_creation_input_tokens") or 0),
    ]


def scan_file(path, fstate, deadline):
    """Incrementally scan one jsonl; update fstate {offset, tot, dedup} in place."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    offset = int(fstate.get("offset", 0))
    if size < offset:          # truncated/rotated: forget this file's contribution
        fstate["offset"], fstate["tot"], fstate["dedup"] = 0, [0, 0, 0, 0], {}
        offset = 0
    if size == offset:
        return
    tot = fstate.setdefault("tot", [0, 0, 0, 0])
    dedup = fstate.setdefault("dedup", {})
    with open(path, "rb") as f:
        f.seek(offset)
        while True:
            if time.time() > deadline:
                break
            line = f.readline()
            if not line:
                break
            if not line.endswith(b"\n") and f.tell() < os.path.getsize(path):
                # partial trailing line still being written: stop before it
                f.seek(-len(line), os.SEEK_CUR)
                break
            fstate["offset"] = f.tell()
            if b'"usage"' not in line or b'"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            u = msg.get("usage")
            if not u:
                continue
            mid = msg.get("id") or d.get("uuid") or ""
            new = usage_tuple(u)
            old = dedup.get(mid)
            if old is not None:            # streamed duplicate: last wins
                for i in range(4):
                    tot[i] += new[i] - old[i]
            else:
                for i in range(4):
                    tot[i] += new[i]
                if len(dedup) >= DEDUP_WINDOW:
                    for k in list(dedup)[: len(dedup) - DEDUP_WINDOW + 1]:
                        del dedup[k]
            dedup[mid] = new


def weighted_of(tot):
    return (WEIGHTS["in"] * tot[0] + WEIGHTS["out"] * tot[1]
            + WEIGHTS["cr"] * tot[2] + WEIGHTS["cw"] * tot[3])


def subagent_file(subdir, agent_id, state):
    """Path of this subagent's own transcript, or None. Cached in state."""
    cache = state.setdefault("agent_files", {})
    p = cache.get(agent_id)
    if p and os.path.isfile(p):
        return p
    if not os.path.isdir(subdir):
        return None
    hits = glob.glob(os.path.join(subdir, "**", "agent-%s.jsonl" % agent_id), recursive=True)
    if not hits:
        return None
    cache[agent_id] = hits[0]
    return hits[0]


def sub_event(kind, sid, agent_id, agent_type, tool, weighted, cap):
    try:
        with open(os.path.join(METER_DIR, "subagent_events.csv"), "a", encoding="utf-8") as f:
            f.write("%s,%s,%s,%s,%s,%s,%.0f,%.0f\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%S"), kind, sid[:8], agent_id,
                (agent_type or "").replace(",", ";"), tool, weighted, cap))
    except Exception:
        pass


def main():
    raw = sys.stdin.read()
    hook = json.loads(raw)
    sid = hook.get("session_id") or ""
    tpath = hook.get("transcript_path") or ""
    tool = hook.get("tool_name") or ""
    if not sid or not tpath or not os.path.exists(tpath):
        return 0

    os.makedirs(METER_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(STATE_DIR, exist_ok=True)
        atomic_write(CONFIG_PATH, DEFAULT_CONFIG)
    cfg = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
    budget = float(cfg["weighted_budget_M"]) * 1e6
    warn_at = budget * float(cfg["warn_frac"])

    state_path = os.path.join(METER_DIR, sid + ".json")
    state = load_json(state_path, {"files": {}, "last_warn": 0})
    files = state.setdefault("files", {})

    deadline = time.time() + SCAN_DEADLINE_S
    targets = [tpath]
    subdir = os.path.join(os.path.dirname(tpath), sid, "subagents")
    if os.path.isdir(subdir):
        targets += glob.glob(os.path.join(subdir, "**", "*.jsonl"), recursive=True)
    for p in targets:
        # normcase+abspath: the same file must never be keyed twice under
        # different separator/case spellings (double-counts the whole session)
        key = os.path.normcase(os.path.abspath(p))
        scan_file(p, files.setdefault(key, {"offset": 0, "tot": [0, 0, 0, 0], "dedup": {}}), deadline)

    tin = sum(f["tot"][0] for f in files.values())
    tout = sum(f["tot"][1] for f in files.values())
    tcr = sum(f["tot"][2] for f in files.values())
    tcw = sum(f["tot"][3] for f in files.values())
    weighted = (WEIGHTS["in"] * tin + WEIGHTS["out"] * tout
                + WEIGHTS["cr"] * tcr + WEIGHTS["cw"] * tcw)

    atomic_write(state_path, state)
    try:
        with open(os.path.join(METER_DIR, "log.csv"), "a", encoding="utf-8") as f:
            f.write("%s,%s,%s,%d,%d,%d,%d,%.0f\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%S"), sid[:8], tool, tin, tout, tcr, tcw, weighted))
    except Exception:
        pass

    summary = ("session %s usage: weighted %.1fM / budget %.1fM "
               "(in %.2fM, out %.2fM, cache-write %.2fM, cache-read %.1fM; "
               "weights in/cw/cr/out = 1/1.25/0.1/5)"
               % (sid[:8], weighted / 1e6, budget / 1e6,
                  tin / 1e6, tout / 1e6, tcw / 1e6, tcr / 1e6))

    if cfg.get("armed", True) and weighted > budget:
        read_path = ((hook.get("tool_input") or {}).get("file_path") or "") if tool == "Read" else ""
        if read_path and HANDOFF_EXEMPT_RE.search(os.path.basename(read_path)):
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "[usage meter] Over budget (" + summary + "). This Read "
                "passes only because it is a handoff or state file: verify the close-out, "
                "report, and stop."}}))
            return 0
        halted = campaign_state_write(
            cfg, hook, "HALTED_BUDGET",
            "weighted %.1fM over the %.1fM budget" % (weighted / 1e6, budget / 1e6))
        sys.stderr.write(
            "[USAGE HARD STOP] BLOCKED - this session is over its token budget.\n"
            + summary + "\n"
            + ("Campaign state set to HALTED_BUDGET; the watchdog will not relaunch "
               "until a human resets the mode.\n" if halted else "")
            + "This gate exists because a runaway lane once burned half a 5h MAX window "
            "in 2h (2026-08-28). Do NOT retry, spawn agents, or work around the block "
            "via other tools. Stop, report current state to the user, and wait.\n"
            "Still allowed: Write/Edit of the handoff, and Read of a handoff or state file "
            "(HANDOFF, RESUME, SESSION_STATE, Claude_ProjectMemory, CAMPAIGN_STATE) to verify it.\n"
            "USER-ONLY bypass: edit " + CONFIG_PATH + " - raise weighted_budget_M or "
            'set "armed": false. Claude must never edit that file itself.\n')
        return 2

    # ---- per-subagent cap (2026-11-07). Only for a call made inside a subagent.
    sub_text = ""
    agent_id = hook.get("agent_id") or ""
    agent_type = hook.get("agent_type") or ""
    try:
        sub_cap = float(cfg.get("subagent_budget_M") or 0) * 1e6
    except (TypeError, ValueError):
        sub_cap = 0.0
    if agent_id and AGENT_ID_RE.match(agent_id) and sub_cap > 0:
        ap = subagent_file(subdir, agent_id, state)
        fst = files.get(os.path.normcase(os.path.abspath(ap))) if ap else None
        if fst:
            sub_w = weighted_of(fst["tot"])
            sub_summary = ("helper %s (%s) in session %s: weighted %.1fM / helper cap %.1fM "
                           "(session total %.1fM / %.1fM)"
                           % (agent_id, agent_type or "subagent", sid[:8], sub_w / 1e6,
                              sub_cap / 1e6, weighted / 1e6, budget / 1e6))
            if cfg.get("armed", True) and sub_w > sub_cap:
                read_path = ((hook.get("tool_input") or {}).get("file_path") or "") if tool == "Read" else ""
                if read_path and HANDOFF_EXEMPT_RE.search(os.path.basename(read_path)):
                    print(json.dumps({"hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": "[usage meter] Helper over its cap (" + sub_summary
                        + "). This Read passes only because it is a handoff or state file: "
                        "verify what you wrote, then return to your parent."}}))
                    return 0
                sub_event("block", sid, agent_id, agent_type, tool, sub_w, sub_cap)
                atomic_write(state_path, state)
                sys.stderr.write(
                    "[HELPER USAGE CAP] BLOCKED - this subagent is over its own token cap.\n"
                    + sub_summary + "\n"
                    "Each helper inside a session is capped (owner ruling, 2026-11-07) because single "
                    "helpers once burned up to 51.6M re-reading images while their siblings used "
                    "about 3M. Do NOT retry, spawn agents, or work around the block via other tools.\n"
                    "Do this now: (1) Write your results so far to disk (Write/Edit are not gated): "
                    "what is finished, what is not, where you stopped, and the exact next step; "
                    "(2) end your turn with a final message to your parent naming that file and "
                    "saying you stopped at the helper cap. The parent session and the other "
                    "helpers are not affected by this block.\n"
                    "USER-ONLY bypass: edit " + CONFIG_PATH + " - raise subagent_budget_M or "
                    'set "armed": false. Claude must never edit that file itself.\n')
                return 2
            warns = state.setdefault("sub_last_warn", {})
            if sub_w > sub_cap * float(cfg["warn_frac"]) and time.time() - warns.get(agent_id, 0) > WARN_COOLDOWN_S:
                warns[agent_id] = time.time()
                atomic_write(state_path, state)
                sub_event("warn", sid, agent_id, agent_type, tool, sub_w, sub_cap)
                sub_text = (
                    "[usage meter - HELPER CAP] " + sub_summary + "\n"
                    "You are past %d%% of this helper's cap; at the cap your tool calls are "
                    "blocked. Finish only the item in flight, write each result to disk as you "
                    "go, and return to your parent within a few calls; do not start new items. "
                    "Do not re-open an image whose earlier read was stripped ([media removed]): "
                    "the note you wrote stands." % int(float(cfg["warn_frac"]) * 100))

    if weighted > warn_at and time.time() - state.get("last_warn", 0) > WARN_COOLDOWN_S:
        state["last_warn"] = time.time()
        atomic_write(state_path, state)
        rotated = campaign_state_write(
            cfg, hook, "ROTATE_PENDING",
            "weighted %.1fM past %d%% of the %.1fM budget"
            % (weighted / 1e6, int(cfg["warn_frac"] * 100), budget / 1e6))
        rotate_line = ("Campaign state set to ROTATE_PENDING: the watchdog relaunches your "
                       "successor from the handoff once this session exits.\n" if rotated else "")
        tail = (
            "You are past %d%% of this session's budget. Per-tool-call cost grows with "
            "context: measured 3.9x from a session's first quarter to its last "
            "(2026-08-31), so continuing here costs multiples of what the same "
            "work costs in a fresh session.\n"
            "STOP TAKING NEW WORK. Finish only the step in flight, then: (1) verify and "
            "checkpoint to disk what is done and what is not, (2) write or update the "
            "handoff file so a fresh session resumes without re-reading, (3) report your "
            "position and STOP. Do not start another episode, batch, lane or gate. "
            "A watchdog relaunches a fresh session from your handoff."
            % int(cfg["warn_frac"] * 100))
        text = "[usage meter - ROTATE NOW] " + summary + "\n" + rotate_line + tail
        print(json.dumps({"systemMessage": text,
                          "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": text + ("\n" + sub_text if sub_text else "")}}))
        return 0
    if sub_text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": sub_text}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail open, always
