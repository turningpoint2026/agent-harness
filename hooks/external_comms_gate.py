#!/usr/bin/env python3
"""External-comms gate.

PreToolUse hook. Blocks (exit 2) any tool call that constitutes an
EXTERNAL COMMUNICATION ACT unless a fresh user-typed greenlight sentinel
exists (written only by comms_greenlight.py from user prompts; machine-origin
prompt text is refused there, so Claude cannot stamp it).

Gated acts:
  - Gmail draft creation/update and any Gmail send-named tool
  - Google Calendar event create/update/delete/respond (invites reach people)
  - Slack send/post/message tools (any MCP tool name containing both
    'slack' and a send-ish verb)
  - Write/Edit/MultiEdit to a path containing 'shared' (case-insensitive),
    '_SHARE' (case-sensitive) or 'OneDrive' -- synced file-drop folders
    and OneDrive collaborator shares count as external communication. Lowercase 'shift_share' style analysis
    filenames do NOT match.
  - Bash/PowerShell commands that copy, move, write, or delete INTO a share
    (added 2026-10-01, /insights). Incident: 2026-09-16 a PowerShell
    Copy-Item put a draft into a collaborator's OneDrive folder after
    the owner had already said "wait, your docx is not ready for share"; the
    path carries no 'shared' and shell writes were never gated, so no hook
    saw it. Only the WRITE
    TARGET is judged, so copying OUT of a share (the normal way to read a
    OneDrive placeholder) passes. A shell target counts as a share when it
    is any OneDrive path, or when a FOLDER above it (not the name being
    written) carries 'shared'/'_SHARE': `x_shared.docx` in _loop_work and
    `mkdir SHARED_copies` pass, `..._SHARED_FOLDER/file` blocks. Targets
    are literal paths, same-command variables ($dst = "...", S="...",
    p = r"...") and Join-Path; a path built inside a script the command
    runs is out of scope and fails open. Replay over 72,035 saved
    file/shell calls (2026-08-29 to 10-01): 5 flags, all 5 real writes
    into a share, in 4 sessions.

Sentinel validity: same session_id, younger than 30 minutes. A block
tells Claude to surface the content to the user and wait for a typed
greenlight; the retry after the stamp passes.

Enforces the work-before-external-comms rule (origin: 2026-07-27, an
agent repackaged files and went straight to drafting a collaborator email). Companion UserPromptSubmit hook: comms_greenlight.py.

Exit codes: 2 = block (stderr goes to Claude), 0 = allow. Any unexpected
error exits 0 -- never wedge the tools.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

STATE_DIR = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "state")
SENTINEL = os.path.join(STATE_DIR, "comms_greenlight.json")
TTL_SECONDS = 30 * 60

EXTERNAL_TOOL_RES = [
    re.compile(r"^mcp__claude_ai_Gmail__(create_draft|update_draft|send\w*)$"),
    re.compile(r"^mcp__claude_ai_Google_Calendar__(create_event|update_event|delete_event|respond_to_event)$"),
    re.compile(r"slack", re.I),  # paired with verb check below
]
SLACK_VERB_RE = re.compile(r"(send|post|message|chat|reply|update)", re.I)
FILE_TOOLS = {"Write", "Edit", "MultiEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

# Paths whose "shared" substring is a NAMING COLLISION, not a live comms
# channel (owner ruling 2026-08-28: a folder named shared_* that was not yet
# shared with anyone made every write to it pure friction). Configure with
# HARNESS_SHARE_ALLOWLIST, os.pathsep-separated path fragments (case-insensitive,
# either slash). REVISIT and REMOVE an entry the day that folder is genuinely shared.
ALLOWLIST_SUBSTRINGS = [a.strip().lower().replace("/", "\\")
                        for a in os.environ.get("HARNESS_SHARE_ALLOWLIST", "").split(os.pathsep) if a.strip()]


# --- shell write targets ----------------------------------------------------

def is_share_target(path: str) -> bool:
    """A shell write target inside a share (rule in the module docstring)."""
    norm = path.replace("/", "\\").rstrip("\\")
    low = norm.lower()
    if any(a in low for a in ALLOWLIST_SUBSTRINGS):
        return False
    if "onedrive" in low:
        return True
    parent = norm.rsplit("\\", 1)[0] if "\\" in norm else ""
    return "shared" in parent.lower() or "_SHARE" in parent


SQ = r"'(?:[^']|'')*'"  # PowerShell single-quoted string, '' is an escaped quote
DQ = r'"[^"]*"'
Q = r"(?:" + DQ + "|" + SQ + r"|[^\s;|&)<>]+)"
CMDPOS = r"(?:^|[;&|({\n])\s*"  # command position: line start or after a separator

PS_ASSIGN_RE = re.compile(r"\$(\w+)\s*=\s*(" + DQ + "|" + SQ + ")")
# `$sh = Join-Path $env:USERPROFILE 'OneDrive - ...'`: keep the joined parts,
# so a target built from $sh still carries the share token when expanded.
PS_ASSIGN_EXPR_RE = re.compile(r"\$(\w+)\s*=\s*(\(?\s*Join-Path\b[^;\n]+)", re.I)
JOIN_PATH_RE = re.compile(r"\(\s*Join-Path\s+([^()]+?)\)", re.I)
SH_ASSIGN_RE = re.compile(r"(?:^|[\s;&(])(\w+)=(" + DQ + r"|'[^']*'|[^\s;&|]+)")
PY_ASSIGN_RE = re.compile(r"(?:^|[\s;(])(\w+)\s*=\s*[rRbBuU]?(\"[^\"\n]*\"|'[^'\n]*')")

REDIRECT_RE = re.compile(r"(?<![0-9&<>])>>?(?!&)\s*(" + Q + ")")
SH_COPY_RE = re.compile(CMDPOS + r"(cp|mv|rsync|robocopy|xcopy|[Cc]opy|[Mm]ove)\s+([^;&|\n}]+)")
SH_TARGET_ALL_RE = re.compile(CMDPOS + r"(rm|rmdir|del|erase|mkdir|touch|tee|truncate|ri|ni)\s+([^;&|\n}]+)")
PS_CMDLET_RE = re.compile(
    r"\b(Copy-Item|Move-Item|Rename-Item|New-Item|Remove-Item|Set-Content|Add-Content"
    r"|Out-File|Clear-Content|Expand-Archive|Compress-Archive|Export-Csv|Start-BitsTransfer)\b([^;|\n}]*)",
    re.I)
PY_SHUTIL_RE = re.compile(r"shutil\.(?:copy2?|copyfile|copytree|move)\(\s*([^,]+?)\s*,\s*([^,)]+)")
PY_OPEN_W_RE = re.compile(r"open\(\s*([^,]+?)\s*,\s*[rRbB]?['\"][wax]")
PY_SAVE_RE = re.compile(r"\.(?:save|write_text|write_bytes|to_csv|to_excel|savefig)\(\s*([^,)]+)")
PY_PATHCALL_WRITE_RE = re.compile(
    r"Path\(\s*([^)]+?)\s*\)\s*\.(?:write_text|write_bytes|mkdir|touch|unlink|rename|replace)\(")
PY_OS_RE = re.compile(r"os\.(?:remove|unlink|rename|replace|makedirs|mkdir|rmdir)\(\s*([^,)]+)(?:\s*,\s*([^,)]+))?")

PS_TARGET_PARAMS = {
    "copy-item": ("destination",), "move-item": ("destination",),
    "expand-archive": ("destinationpath",), "compress-archive": ("destinationpath",),
    "start-bitstransfer": ("destination",),
}
PS_PATH_PARAMS = ("path", "literalpath", "filepath", "destination", "destinationpath", "newname")

# One argument; a PowerShell array "a","b" stays one token (split later).
_ATOM = "(?:" + DQ + "|" + SQ + r"""|[^\s,"']+)"""
TOKEN_RE = re.compile(_ATOM + r"(?:\s*,\s*" + _ATOM + ")*")
ELEM_RE = re.compile(DQ + "|" + SQ + r"|[^\s,]+")


def _strip(tok: str) -> str:
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        q = tok[0]
        tok = tok[1:-1]
        if q == "'":
            tok = tok.replace("''", "'")
    return tok


def _join_flat(expr: str) -> str:
    """`Join-Path A 'b'` -> `A\\b` (quotes dropped), so share tokens survive."""
    expr = re.sub(r"^\(?\s*Join-Path\s+", "", expr, flags=re.I).rstrip(") ")
    parts = [_strip(p) for p in TOKEN_RE.findall(expr) if not p.startswith("-")]
    return "\\".join(parts) if parts else expr


def _vars(cmd: str) -> dict:
    v = {}
    for m in PS_ASSIGN_EXPR_RE.finditer(cmd):
        v[m.group(1)] = _join_flat(m.group(2))
    for rx in (PS_ASSIGN_RE, SH_ASSIGN_RE, PY_ASSIGN_RE):
        for m in rx.finditer(cmd):
            v[m.group(1)] = _strip(m.group(2))
    return v


def _resolve(tok: str, v: dict) -> str:
    tok = _strip(tok)
    tok = re.sub(r"\$\{(\w+)\}|\$(\w+)",
                 lambda m: v.get(m.group(1) or m.group(2), m.group(0)), tok)
    if tok in v:  # python bare variable name
        tok = v[tok]
    return tok


def _elems(tok: str) -> list:
    return ELEM_RE.findall(tok) or [tok]


def write_targets(cmd: str) -> list:
    """Literal (or same-command-variable) paths this command writes to."""
    v = _vars(cmd)
    cmd = JOIN_PATH_RE.sub(lambda m: '"' + _join_flat(m.group(1)) + '"', cmd)
    out = []
    for m in REDIRECT_RE.finditer(cmd):
        out.append(m.group(1))
    for m in SH_COPY_RE.finditer(cmd):
        verb = m.group(1).lower()
        args = [a for a in TOKEN_RE.findall(m.group(2)) if not a.startswith("-")
                and not (verb in ("robocopy", "xcopy") and a.startswith("/"))]
        if verb == "robocopy" and len(args) >= 2:
            out.append(args[1])
        elif len(args) >= 2:
            out.append(args[-1])
        if verb in ("mv", "move") and len(args) >= 2:
            out.extend(args[:-1])  # a move also removes from the source
    for m in SH_TARGET_ALL_RE.finditer(cmd):
        out.extend(a for a in TOKEN_RE.findall(m.group(2)) if not a.startswith("-"))
    for m in PS_CMDLET_RE.finditer(cmd):
        cmdlet = m.group(1).lower()
        toks = TOKEN_RE.findall(m.group(2))
        named, pos, i = {}, [], 0
        while i < len(toks):
            t = toks[i]
            if t.startswith("-") and len(t) > 1 and not t[1].isdigit():
                key = t[1:].rstrip(":").lower()
                if i + 1 < len(toks) and not toks[i + 1].startswith("-"):
                    named[key] = toks[i + 1]
                    i += 2
                    continue
                i += 1
                continue
            pos.append(t)
            i += 1
        if cmdlet in PS_TARGET_PARAMS:
            dest = None
            for k in PS_TARGET_PARAMS[cmdlet]:
                if k in named:
                    dest = named[k]
            if dest is None:
                if "path" in named or "literalpath" in named:
                    dest = pos[0] if pos else None
                elif len(pos) >= 2:
                    dest = pos[1]
            if dest is not None:
                out.append(dest)
            if cmdlet == "move-item":
                src = named.get("path") or named.get("literalpath") or (pos[0] if pos else None)
                if src:
                    out.append(src)
        else:
            for k in PS_PATH_PARAMS:
                if k in named:
                    out.append(named[k])
            if pos:
                out.append(pos[0])
    for m in PY_SHUTIL_RE.finditer(cmd):
        out.append(m.group(2))
    for rx in (PY_OPEN_W_RE, PY_SAVE_RE, PY_PATHCALL_WRITE_RE):
        for m in rx.finditer(cmd):
            out.append(m.group(1))
    for m in PY_OS_RE.finditer(cmd):
        out.append(m.group(1))
        if m.group(2):
            out.append(m.group(2))
    flat = []
    for t in out:
        if t and t.strip():
            flat.extend(_elems(t) if "," in t else [t])
    return [_resolve(t, v) for t in flat]


def shell_share_write(cmd: str) -> str | None:
    """Return the first share path this command writes to, or None."""
    if not re.search(r"onedrive|_SHARE|shared", cmd, re.I):
        return None
    for t in write_targets(cmd):
        if is_share_target(t):
            return t
    return None


# --- decision ---------------------------------------------------------------

def is_external_act(tool: str, tool_input: dict) -> str | None:
    """Return a human-readable reason if this call is an external act."""
    if tool in FILE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        norm = path.lower().replace("/", "\\")
        if any(a in norm for a in ALLOWLIST_SUBSTRINGS):
            return None
        if "shared" in norm or "_SHARE" in path or "onedrive" in norm:
            return "%s to shared-channel path: %s" % (tool, path)
        return None
    if tool in SHELL_TOOLS:
        target = shell_share_write(tool_input.get("command") or "")
        if target:
            return "%s command writes into a share: %s" % (tool, target)
        return None
    for rx in EXTERNAL_TOOL_RES:
        if rx.search(tool):
            if "slack" in rx.pattern:
                if "slack" in tool.lower() and SLACK_VERB_RE.search(tool):
                    return "Slack outbound tool: %s" % tool
                return None
            return "external-communication tool: %s" % tool
    return None


def greenlight_valid(session: str) -> tuple[bool, str]:
    if not os.path.exists(SENTINEL):
        return False, "no greenlight has been stamped"
    try:
        with open(SENTINEL, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False, "greenlight sentinel unreadable"
    age = time.time() - float(data.get("stamped_at_epoch", 0))
    if data.get("session_id") != session:
        return False, "greenlight belongs to a different session"
    if age > TTL_SECONDS:
        return False, "greenlight expired (%d min old, limit 30)" % int(age / 60)
    return True, ""


def main() -> None:
    data = json.loads(sys.stdin.read() or "{}")
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    session = data.get("session_id") or ""

    reason = is_external_act(tool, tool_input)
    if reason is None:
        sys.exit(0)

    ok, why_not = greenlight_valid(session)
    if ok:
        sys.exit(0)

    sys.stderr.write(
        "[external-comms-gate] BLOCKED: " + reason + "\n"
        "Why: " + why_not + ".\n"
        "This is an external communication act. Required pipeline: (1) do the\n"
        "substantive work, (2) show it to the user and iterate, (3) the user types\n"
        "an explicit greenlight in their own prompt ('go ahead', 'send it',\n"
        "'GO EMAIL', or 'GO SHARE' for a file into a share), (4) retry this\n"
        "call within 30 minutes.\n"
        "For a share write: re-read the user's LATEST prompt first. If it changed or\n"
        "paused the request ('wait', 'not ready', 'just build'), stop: build\n"
        "locally and say where the file is. Claude builds; the user moves.\n"
        "Claude cannot satisfy this gate itself: the sentinel is stamped only\n"
        "by the UserPromptSubmit hook from a phrase the user actually typed.\n"
        "Do NOT work around the gate (no Python script that writes the share,\n"
        "no alternate send paths). Surface the content in chat and wait for\n"
        "the greenlight.\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
