#!/usr/bin/env python3
"""Turn-end gate: completion claims without a read-back (R1) and autopilot
endings (R2). Stop hook (harness redesign 2026-09-06, plan step 3, D2 + D3).

What it sees: the turn since the last real user prompt, parsed from the
transcript the way response_length_log.py does: every tool call in order
(name + command) and the reply text. Tool calls are classified as
  ACTION    Write, Edit, MultiEdit, NotebookEdit, SendMessage, Agent, Task,
            Workflow, or a shell command with a write/launch verb;
  READ-BACK Read, Grep, Glob, an MCP read tool (get_thread, get_message,
            read_email, ...), or a shell command with a read/query verb
            (Get-Process, Test-Path, pdftotext, pdftoppm, verify_docx.py,
            render_check.py, Get-Content, dir, ls, wc, ...).
The primitives are the ones the replay script used to tune the rules
(stage 1 narrowed); the private suite asserted parity with that script.

R1 (all modes): the reply carries a completion claim about this turn (a
line-initial Done/Opened/Sent/Fixed/Ready/Running/Launched/Asked, a
line-initial checkmark whose line carries one of those words, or "has been
sent" / "is done" / "confirmed") whose own sentence carries no label
(question-mark marker, [UNVERIFIED, [INFERRED, "unverified"), AND the last
ACTION has no READ-BACK after it, or a message to another session is claimed
with no SendMessage call in the turn.
R2 (autopilot only, never after a ROTATE order): the reply ends with a
question mark, or carries one of the ask-phrases ("want me to", "should I",
"say go", "your call", "let me know which", "or 'end of day'", "unless you
say otherwise", "needs your ruling"), or the turn made zero ACTION calls
with no [BLOCKED: reason] while the campaign state names an unclaimed item
(a CAMPAIGN_STATE.json above cwd in mode RUNNING with nothing in flight).
The five-field status line ending "needs you: none" is an allowed ending.
R3 (all modes, 2026-09-24, owner: "false sent needs to be fixed"): the reply
announces a message or a launch that no tool call made. NOW claims
("Asking the data session ...", "Dispatching three verification windows",
"I'm messaging the lane") need the tool in THIS turn: SendMessage for a
message; Agent / Task / Workflow / SendMessage / RemoteTrigger / CronCreate or
a shell launch (Start-Process, start, nohup, claude -p, schtasks, trailing &)
for a launch. PAST claims ("I sent the lane ...", "Dispatched 3 agents") may
recap earlier work, so the tool may sit in this turn or the previous three;
an Agent call counts for "told the lane" because a lane's prompt tells it.
Future, conditional and negated phrasings, questions, code spans, quotes and
table rows are skipped. Replay over 2,506 saved turns: 8 would-bounce (0.32
percent), 7 real, among them both known incidents ("Asking the transcription
session" before "did you ask?"; "Dispatching three verification windows"
before "i only see one agent working below?"). Source: a replay script
run over the saved transcripts (not included).

Per-rule flags live in turn_end_gate.json beside this file (copied by the
deployer): {"R1": bool, "R2": bool, "hard_mode": bool, "shadow_log": bool}.
A rule ships as a bounce only after its replay table sits under the pass
mark (plan step 1). Stage 1 outcome: both rules FAILED their narrowed
replay, so both flags are false and the rules ship INJECT-ONLY through
response_style_contract.py; with shadow_log the hook still logs every
would-bounce to ~/.claude/state/turn_end_gate_log.csv so the day-14 review
has live numbers.

One bounce per turn, two guards: stop_hook_active on the payload, and the
count of this hook's own prior "Stop hook feedback: [python ...turn_end_gate.py]"
entries inside the current turn (harness-changes register, entry 3). On the retry
Stop the hook logs the retry outcome (check added / label added / neither)
and, in hard mode only, arms a second bounce for the next turn's first Stop.
Tripwire mode (hard_mode false): the retry passes whatever it carries.

Bounce messages are plan step 3 verbatim. Fails open: any unexpected
error exits 0.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "state")
LOG_PATH = os.path.join(STATE_DIR, "turn_end_gate_log.csv")
CONFIG_PATH = os.environ.get("CLAUDE_TURN_END_GATE_CONFIG") or os.path.join(HERE, "turn_end_gate.json")
DEFAULT_CONFIG = {"R1": False, "R2": False, "R3": False, "hard_mode": False, "shadow_log": True}
SELF_TAG = "turn_end_gate.py"

sys.path.insert(0, HERE)
try:
    from session_mode import effective_mode
except Exception:  # pragma: no cover
    def effective_mode(sid, transcript_path=None):
        return "default", None, None

# ---------------------------------------------------------------- primitives (replay_rules.py, stage 1 narrowed)
ACTION_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "SendMessage", "Agent", "Task", "Workflow"}
READBACK_TOOLS = {"Read", "Grep", "Glob"}
SHELL_TOOLS = {"Bash", "PowerShell"}
READBACK_MCP = re.compile(r"^mcp__.*__(get_|read_|list_|search_|local_search|sync_status|tabs_context|find$|get_page_text)")
ACTION_MCP = re.compile(r"^mcp__.*__(send_|create_|update_|delete_|forward|reply|trash_|label_|share_)")
# The named read-back scripts win over the generic "python x.py" launch verb.
READ_SCRIPT = re.compile(r"(verify_docx|render_check)\.py\b", re.I)
# Word boundaries per alternative: a trailing \b after whitespace or an emoji never matches.
WRITE_VERB = re.compile(
    r"(?:^|[\s;&|(])(?:"
    r"(?:cp|mv|rm|rmdir|mkdir|touch|tee|pandoc|ffmpeg|schtasks|xcopy|robocopy|wget|Invoke-Item|Copy-Item|Move-Item|Remove-Item|New-Item|Set-Content|Add-Content|Out-File|Start-Process)\b"
    r"|sed -i\b|node\s|start\s|explorer(?:\.exe)?\b|curl\s+-o\b"
    r"|python[3]?\s+(?!-c\s*[\"']print)\S+\.py\b|py\s+\S+\.py\b"
    r"|git\s+(?:commit|push|add|rm|mv|checkout|reset)\b"
    r"|\$\w+\.(?:Save|SaveAs|SaveAs2|Close)\("  # Word/Excel COM edits (stage 1)
    r")|>>?\s*\S", re.I)
READ_VERB = re.compile(
    r"(?:^|[\s;&|(])(?:"
    r"(?:cat|head|tail|less|grep|rg|find|ls|dir|wc|stat|file|diff|type|tree|md5sum|sha\d+sum|pdftotext|pdftoppm|pdfinfo|tasklist|wmic|jq|sqlite3"
    r"|Get-Content|Get-ChildItem|Get-Item|Get-Process|Test-Path|Select-String|Get-Date|Get-CimInstance|Get-ScheduledTask|Measure-Object)\b"
    r"|verify_docx\.py\b|render_check\.py\b|ps\s|python[3]?\s+-c\b|git\s+(?:status|log|diff|show)\b"
    r")", re.I)
DONE_WORDS = r"(?:Done|Opened|Sent|Fixed|Ready|Running|Launched|Asked)"
COMPLETION_LINE = re.compile(
    r"(?m)^[ \t]*(?:[-*>]\s*|\d+\.\s*)?(?:\*\*)?(?:"
    r"✅[^\n]*?\b" + DONE_WORDS + r"\b"
    r"|" + DONE_WORDS + r"\b)", re.I)
COMPLETION_ANY = re.compile(r"\b(has been sent|is done|confirmed)\b", re.I)
LABEL = re.compile(r"❓|\[UNVERIFIED|\[INFERRED|\[NEEDS SOURCE|\[GAP|\bunverified\b", re.I)
PEER_CLAIM = re.compile(r"\b(sent|messaged|told|pinged|notified)\b[^.\n]{0,50}\b(session|peer|lane|worker)\b", re.I)
R2_PHRASES = ["want me to", "should I", "say go", "your call", "let me know which",
              "or 'end of day'", "unless you say otherwise", "needs your ruling"]
R2_RE = re.compile("|".join(re.escape(p) for p in R2_PHRASES), re.I)
STATUS_LINE = re.compile(r"needs you:\s*none\s*$", re.I)
BLOCKED = re.compile(r"\[BLOCKED[:\]]")

# Stage 3 (plan step 6, R1-to-render_check link): a completion claim about a PDF or
# figure deliverable is satisfied only by a render_check.py manifest for that file
# whose every page PNG this session has Read (read ledger). pdftoppm by hand plus a
# Read of one page no longer counts: "4 renders out of 45" logged as visually
# inspected is the incident (a deliverables-register entry).
PDF_CLAIM = re.compile(r"\.pdf\b|\bpdfs?\b|\bfigures?\b|\bfig\.?\s*\d|\.png\b", re.I)
FILE_TOKEN = re.compile(r"[\w./\\:-]+\.(?:pdf|png)\b", re.I)

R1_MESSAGE = ("Name the check that proves it, or put [UNVERIFIED: reason] inside that sentence. "
              "PDF or figure: python render_check.py <file>, then Read every page PNG it lists. Docx: verify_docx.py. "
              "Process: query the PID and the heartbeat mtime. Folder or window: "
              "'command issued, confirm you see it'. Email: get_thread. Peer session: its reply. "
              "File: re-read the lines.")
R2_MESSAGE = ("Autopilot. Take the option you recommended and continue with {next_item}. "
              "A progress line is welcome; a question is not.")
R3_MESSAGE = ("Your reply {why}. Either make the call now (SendMessage to the session, Agent or "
              "Workflow for a lane, a launch command for a window) and report its result, or rewrite "
              "the sentence so it says the message or launch has NOT happened.")

# R3 primitives (replayed as scratchpad r3_rule.py v2, 2026-09-24)
R3_RECENT_TURNS = 3
_R3_MSG_TARGET = (r"(?:the\s+|that\s+|this\s+|each\s+|both\s+|all\s+|\d+\s+)?(?:[\w-]+\s+){0,3}?"
                  r"(?:session|sibling|peer|orchestrator|lane|worker|front|S\d\d(?:E\d\d)?\b)")
_R3_RUN_TARGET = (r"(?:the\s+|\d+\s+|two\s+|three\s+|four\s+|five\s+|six\s+|eight\s+|nine\s+)?(?:[\w-]+\s+){0,3}?"
                  r"(?:agents?|lanes?|workers?|windows?|subagents?|verifiers?|readers?|batch(?:es)?|sessions?)\b")
_R3_LEAD = r"(?:^|[.!:]\s+|\n\s*(?:[-*]\s+)?|\bI'?m\s+|\bnow\s+)"
_R3_LINE = r"(?:^|\n\s*(?:[-*]\s+)?(?:✅\s*)?)"
R3_NOW_MSG = re.compile(_R3_LEAD + r"(?:asking|messaging|pinging|telling|sending)\s+" + _R3_MSG_TARGET, re.I)
R3_NOW_RUN = re.compile(_R3_LEAD + r"(?:dispatching|launching|spawning|starting|kicking off|firing off)\s+" + _R3_RUN_TARGET, re.I)
R3_PAST_MSG = re.compile(r"\b(?:I(?:'ve| have)?\s+)(?:sent|messaged|pinged|asked|told|notified)\s+" + _R3_MSG_TARGET
                         + r"|" + _R3_LINE + r"(?:Sent|Messaged|Pinged|Asked|Told)\s+" + _R3_MSG_TARGET, re.I)
R3_PAST_RUN = re.compile(r"\b(?:I(?:'ve| have)?\s+)(?:dispatched|launched|spawned|kicked off|fired off)\s+" + _R3_RUN_TARGET
                         + r"|" + _R3_LINE + r"(?:Dispatched|Launched|Spawned)\s+" + _R3_RUN_TARGET, re.I)
R3_HEDGE = re.compile(r"\b(?:will|'ll|to|should|could|would|can|going to|not|n't|never|if|once|when|before|after|"
                      r"until|want me|shall|without|instead of)\s*$", re.I)
R3_MSG_TOOLS = {"SendMessage"}
R3_RUN_TOOLS = {"Agent", "Task", "Workflow", "SendMessage", "RemoteTrigger", "CronCreate"}
R3_LAUNCH_SHELL = re.compile(r"Start-Process|\bstart\s|\bnohup\b|claude\s+(?:-p|--print)\b|schtasks|&\s*$", re.I | re.M)


def classify(name, cmd):
    if name in ACTION_TOOLS:
        return "ACTION"
    if name in READBACK_TOOLS:
        return "READBACK"
    if name and READBACK_MCP.match(name):
        return "READBACK"
    if name and ACTION_MCP.match(name):
        return "ACTION"
    if name in SHELL_TOOLS:
        c = cmd or ""
        if READ_SCRIPT.search(c):
            return "READBACK"
        if WRITE_VERB.search(c):
            return "ACTION"
        if READ_VERB.search(c):
            return "READBACK"
    return None


def sentence_at(text, pos):
    a = max(text.rfind("\n", 0, pos), text.rfind(". ", 0, pos))
    a = 0 if a < 0 else a + 1
    b = min(x for x in (text.find("\n", pos), text.find(". ", pos), len(text)) if x >= 0)
    return text[a:b]


def claims_in(reply):
    claims = [(m.start(), m.group(0).strip()) for m in COMPLETION_LINE.finditer(reply)]
    claims += [(m.start(), m.group(0)) for m in COMPLETION_ANY.finditer(reply)]
    return claims


def unlabeled_claims(reply):
    return [(p, w) for p, w in claims_in(reply) if not LABEL.search(sentence_at(reply, p))]


def _norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p))).replace("\\", "/")


def _stem(p):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.splitext(os.path.basename(p))[0])[:60].lower()


def ledger_paths(sid):
    """Paths this session has Read (read_ledger.py's JSONL)."""
    d = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "hook_state")
    p = os.path.join(d, "read_ledger_%s.jsonl" % re.sub(r"[^A-Za-z0-9_-]", "_", str(sid))[:80])
    seen = set()
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    seen.add(_norm(json.loads(line)["path"]))
                except Exception:
                    continue
    except OSError:
        pass
    return seen


def render_manifests(sid):
    """Every render_manifest.json render_check.py wrote for this session: the
    session scratchpad(s), ~/.claude/state/render_check/<sid>, plus any dirs in
    CLAUDE_RENDER_CHECK_DIRS (tests)."""
    import glob as _glob
    dirs = []
    env = os.environ.get("CLAUDE_RENDER_CHECK_DIRS")
    if env:
        dirs += [d for d in env.split(os.pathsep) if d]
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TMPDIR") or os.environ.get("TMP")
    if base and sid:
        dirs += _glob.glob(os.path.join(base, "Temp", "claude", "*", str(sid), "scratchpad", "render_check"))
    dirs.append(os.path.join(STATE_DIR, "render_check", str(sid)))
    out = []
    for d in dirs:
        for mp in _glob.glob(os.path.join(d, "*", "render_manifest.json")):
            try:
                with open(mp, encoding="utf-8") as fh:
                    man = json.load(fh)
                if isinstance(man, dict) and man.get("pages"):
                    out.append(man)
            except Exception:
                continue
    return out


def pdf_claim_gap(sid, reply, claims):
    """'' when every PDF/figure completion claim in the reply is backed by a manifest
    whose pages were all Read; else the reason."""
    pdf_claims = [(p, w) for p, w in claims if PDF_CLAIM.search(sentence_at(reply, p))]
    if not pdf_claims:
        return ""
    named = {_stem(t) for p, _ in pdf_claims for t in FILE_TOKEN.findall(sentence_at(reply, p))}
    manifests = render_manifests(sid)
    if not manifests:
        return "pdf/figure claim, no render_check manifest"
    seen = ledger_paths(sid)
    relevant = [m for m in manifests if not named or _stem(m.get("source", "")) in named]
    if not relevant:
        return "pdf/figure claim, no manifest for %s" % ", ".join(sorted(named))
    worst = None
    for m in relevant:
        pages = m.get("pages") or []
        viewed = sum(1 for pg in pages if _norm(pg.get("png", "")) in seen)
        if viewed < len(pages):
            worst = "pdf/figure claim, manifest %s: %d of %d pages Read" % (
                os.path.basename(m.get("source", "?")), viewed, len(pages))
    return worst or ""


def r1_fire(tools, reply, sid=None):
    """(fires, why). tools = [(name, cmd), ...] in order."""
    claims = unlabeled_claims(reply)
    if not claims:
        return False, ""
    kinds = [classify(n, c) for n, c in tools]
    last_action = max((i for i, k in enumerate(kinds) if k == "ACTION"), default=-1)
    last_read = max((i for i, k in enumerate(kinds) if k == "READBACK"), default=-1)
    seq = last_action >= 0 and last_read < last_action
    peer = bool(PEER_CLAIM.search(reply)) and not any(n == "SendMessage" for n, _ in tools)
    gap = pdf_claim_gap(sid, reply, claims) if (sid is not None and last_action >= 0) else ""
    if seq or peer or gap:
        why = "claim '%s'" % claims[0][1][:60].replace("\n", " ")
        if seq:
            why += "; last ACTION %s unread-back" % tools[last_action][0]
        elif peer:
            why += "; peer claim, no SendMessage"
        if gap:
            why += "; " + gap
        return True, why
    return False, ""


def r2_fire(tools, reply, queue_item=None):
    lines = [l for l in reply.splitlines() if l.strip()]
    last = lines[-1].strip() if lines else ""
    if STATUS_LINE.search(last):
        return False, ""
    if last.endswith("?"):
        return True, "ends with ?"
    m = R2_RE.search(reply)
    if m:
        return True, "phrase '%s'" % m.group(0)
    if queue_item and not any(classify(n, c) == "ACTION" for n, c in tools) and not BLOCKED.search(reply):
        return True, "zero ACTION calls, no [BLOCKED], queue names %s" % queue_item
    return False, ""


def _r3_claims(rx, text):
    out = []
    for m in rx.finditer(text):
        ls = text.rfind("\n", 0, m.start()) + 1
        le = text.find("\n", m.end())
        line = text[ls: le if le >= 0 else len(text)]
        if R3_HEDGE.search(text[max(ls, m.start() - 25):m.start()]) or line.rstrip().endswith("?"):
            continue
        if line.lstrip().startswith((">", "|")):
            continue
        out.append(" ".join(m.group(0).split()).lstrip(".!: "))
    return out


def _r3_has(tools, names, shell=False):
    for n, cmd in tools:
        if n in names:
            return True
        if shell and n in SHELL_TOOLS and R3_LAUNCH_SHELL.search(cmd or ""):
            return True
    return False


def r3_fire(tools, reply, recent_tools=()):
    """(fires, why). tools = this turn's calls; recent_tools = the previous
    R3_RECENT_TURNS turns' calls, flattened."""
    text = re.sub(r"```.*?```", "", reply, flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    # Quoted phrases are examples, not claims (first live false bounce, 2026-09-24:
    # a reply describing R3 quoted a past-tense example line inside double quotes).
    text = re.sub(r"\"[^\"\n]{0,200}\"|“[^”\n]{0,200}”", "", text)
    both = list(tools) + list(recent_tools)
    whys = []
    c = _r3_claims(R3_NOW_MSG, text)
    if c and not _r3_has(tools, R3_MSG_TOOLS):
        whys.append("says '%s' but this turn made no SendMessage call" % c[0][:70])
    c = _r3_claims(R3_NOW_RUN, text)
    if c and not _r3_has(tools, R3_RUN_TOOLS, shell=True):
        whys.append("says '%s' but this turn launched nothing" % c[0][:70])
    c = _r3_claims(R3_PAST_MSG, text)
    if c and not _r3_has(both, R3_RUN_TOOLS):
        whys.append("claims '%s' with no SendMessage or Agent call in the last %d turns" % (c[0][:70], R3_RECENT_TURNS + 1))
    c = _r3_claims(R3_PAST_RUN, text)
    if c and not _r3_has(both, R3_RUN_TOOLS, shell=True):
        whys.append("claims '%s' with no launch in the last %d turns" % (c[0][:70], R3_RECENT_TURNS + 1))
    return bool(whys), "; ".join(whys)


# ---------------------------------------------------------------- transcript
def parse_turn(transcript_path):
    """Return dict(tools=[(name, cmd)], reply=str, own_bounces=int,
    tools_after_bounce=[(name, cmd)], reply_after_bounce=str) for the turn
    since the last real user prompt. A real prompt = user entry with plain
    string content and no isMeta flag; Stop-hook feedback entries are user
    entries with isMeta set, and count when they name this hook."""
    tools, texts, own = [], [], 0
    tools_ab, texts_ab = [], []
    history = []   # tool lists of earlier turns, newest last (R3)
    try:
        fh = open(transcript_path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("isSidechain"):
                continue
            typ = obj.get("type")
            content = (obj.get("message") or {}).get("content")
            if typ == "user" and isinstance(content, str):
                if obj.get("isMeta") or content.startswith("Stop hook feedback"):
                    if content.startswith("Stop hook feedback") and SELF_TAG in content[:400]:
                        own += 1
                        tools_ab, texts_ab = [], []
                    continue
                history = (history + [tools])[-R3_RECENT_TURNS:]
                tools, texts, own = [], [], 0
                tools_ab, texts_ab = [], []
            elif typ == "assistant" and isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and (b.get("text") or "").strip():
                        texts.append(b["text"])
                        if own:
                            texts_ab.append(b["text"])
                    elif b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        cmd = inp.get("command") if isinstance(inp, dict) else ""
                        t = (b.get("name") or "", (cmd or "")[:400])
                        tools.append(t)
                        if own:
                            tools_ab.append(t)
    return {"tools": tools, "reply": "\n".join(texts).strip(), "own_bounces": own,
            "recent_tools": [t for turn_tools in history for t in turn_tools],
            "tools_after_bounce": tools_ab, "reply_after_bounce": "\n".join(texts_ab).strip()}


# ---------------------------------------------------------------- side inputs
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        for k in DEFAULT_CONFIG:
            if k in d:
                cfg[k] = bool(d[k])
    except Exception:
        pass
    return cfg


def rotate_recorded(sid):
    """usage_hard_stop.py stamps last_warn in usage_meter/<sid>.json when the
    70 percent ROTATE warning fires; that is the ROTATE order (plan step 3)."""
    try:
        p = os.path.join(STATE_DIR, "usage_meter", "%s.json" % sid)
        with open(p, encoding="utf-8") as fh:
            return bool(json.load(fh).get("last_warn"))
    except Exception:
        return False


def campaign_queue_item(cwd):
    """The unclaimed campaign item, if a CAMPAIGN_STATE.json above cwd is in
    mode RUNNING with nothing in flight; else None."""
    d = os.path.abspath(cwd or "")
    for _ in range(6):
        p = os.path.join(d, "CAMPAIGN_STATE.json")
        if os.path.isfile(p):
            try:
                st = json.load(open(p, encoding="utf-8"))
            except Exception:
                return None
            if str(st.get("mode", "")).upper() != "RUNNING" or st.get("in_flight"):
                return None
            note = str(st.get("mode_note") or "")
            m = re.search(r"Next unit:\s*([^.\n]{3,80})", note)
            return (m.group(1).strip() if m else "the next queue item")
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def log_row(sid, rule, kind, why, outcome, mode):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        new = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["timestamp_utc", "session_id", "rule", "kind", "why", "outcome", "mode"])
            w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        sid, rule, kind, why, outcome, mode])
    except OSError:
        pass


def pending_path(sid):
    return os.path.join(STATE_DIR, "turn_end_gate_pending_%s.json" % sid)


def retry_outcome(turn):
    """What the retry carried after this hook's bounce."""
    if any(classify(n, c) == "READBACK" for n, c in turn["tools_after_bounce"]):
        return "check added"
    after = turn["reply_after_bounce"]
    if after and claims_in(after) and not unlabeled_claims(after):
        return "label added"
    if after and LABEL.search(after):
        return "label added"
    return "neither"


# ---------------------------------------------------------------- main
def main():
    # Hook pipes default to cp1252 on Windows; the R1 message carries a
    # non-ASCII marker, and an encode error here would fail open silently.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    transcript = payload.get("transcript_path") or ""
    if not transcript or not os.path.exists(transcript):
        return 0
    sid = payload.get("session_id", "nosession")
    cfg = load_config()
    turn = parse_turn(transcript)
    if not turn:
        return 0
    mode, since, signal = effective_mode(sid, transcript)

    pend = pending_path(sid)
    pending = None
    if os.path.exists(pend):
        try:
            pending = json.load(open(pend, encoding="utf-8"))
        except Exception:
            pending = None

    # The retry Stop: this hook already bounced inside this turn.
    if turn["own_bounces"] >= 1:
        if pending and pending.get("stage") == "bounced":
            outcome = retry_outcome(turn)
            log_row(sid, pending.get("rule", "?"), "retry", pending.get("why", ""), outcome, mode)
            if cfg["hard_mode"] and outcome == "neither":
                pending.update({"stage": "rebounce"})
                json.dump(pending, open(pend, "w", encoding="utf-8"))
            else:
                try:
                    os.remove(pend)
                except OSError:
                    pass
        return 0
    if payload.get("stop_hook_active"):
        return 0

    # Hard mode: the previous turn's retry carried nothing; bounce once more.
    if pending and pending.get("stage") == "rebounce" and cfg["hard_mode"]:
        try:
            os.remove(pend)
        except OSError:
            pass
        log_row(sid, pending.get("rule", "?"), "bounce-hard", pending.get("why", ""), "", mode)
        sys.stderr.write("[TURN END GATE %s, hard mode: the retry carried neither a check nor a label] "
                         % pending.get("rule", "?") + pending.get("message", "") + "\n")
        return 2

    tools, reply = turn["tools"], turn["reply"]
    if not reply:
        return 0
    fired = []
    ok, why = r1_fire(tools, reply, sid=sid)
    if ok:
        fired.append(("R1", why, R1_MESSAGE))
    ok, why = r3_fire(tools, reply, turn.get("recent_tools") or [])
    if ok:
        fired.append(("R3", why, R3_MESSAGE.format(why=why)))
    if mode == "autopilot" and not rotate_recorded(sid):
        item = campaign_queue_item(payload.get("cwd") or "")
        ok, why = r2_fire(tools, reply, item)
        if ok:
            fired.append(("R2", why, R2_MESSAGE.format(next_item=item or "the next item you named")))
    if not fired:
        return 0

    for rule, why, message in fired:
        if cfg.get(rule):
            log_row(sid, rule, "bounce", why, "", mode)
            try:
                os.makedirs(STATE_DIR, exist_ok=True)
                json.dump({"stage": "bounced", "rule": rule, "why": why, "message": message},
                          open(pend, "w", encoding="utf-8"))
            except OSError:
                pass
            sys.stderr.write("[TURN END GATE %s] %s\n(%s; tripwire: the retry passes; one bounce per turn)\n"
                             % (rule, message, why))
            return 2
        if cfg.get("shadow_log"):
            log_row(sid, rule, "shadow", why, "", mode)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
