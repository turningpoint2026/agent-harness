#!/usr/bin/env python3
r"""hook_dispatch.py - one process per hook EVENT instead of one per hook script.

Origin (2026-09-06): the owner reported console windows popping up and closing
rapidly, making it impossible to type. Claude Code 2.1.263 on Windows
shows a console for every hook command; a file edit fired 14 PreToolUse commands
and a prompt 10 UserPromptSubmit commands, each its own window. This dispatcher
is the single command wired per event; it runs the registered hook scripts as
console-less children (proc_quiet), in parallel threads, and folds their
results into one answer for the host. The hook scripts are unchanged and still
testable one by one.

    python hook_dispatch.py <UserPromptSubmit|PreToolUse|PostToolUse|Stop> [--agent claude|codex]

Registry: hook_registry.json beside this file, written by deploy_hooks.py from
its HOOKS list (event, matcher, command, agents) plus the CLAUDE.md sync pair.
Matchers keep Claude Code's semantics (a regex searched in the tool name;
empty = every tool). The UserPromptSubmit run also performs the CLAUDE.md sync
copy that used to be its own PowerShell hook, and regenerates ~/.codex/AGENTS.md
when the master is newer (Codex integration part 1; the builder script is not
included in this repository, so that step is skipped here).

Two hosts, one hook family (Codex integration part 2, 2026-09-07). The
same dispatcher is wired in Codex's hooks.json with `--agent codex`; for that
host it TRANSLATES the payload into the Claude shapes the hooks were written
for, then runs the same scripts:
  * shell calls (`exec_command`, `shell`, or the code-mode `exec` tool whose
    input is JavaScript calling tools.exec_command) -> one `Bash` payload per
    command with tool_input.command;
  * `apply_patch` -> one `Write` (Add File) or `Edit` (Update File: the changed
    regions as old_string/new_string, the reconstructed file as _codex_after)
    payload per file, through patch_parse.py; a Move adds a Write for the
    destination; a Delete is a Write of "" flagged _codex_op=delete;
  * PostToolUse shell calls also synthesize `Read` payloads for every file the
    command read (Get-Content, cat, type, sed -n, head, tail ...), full only
    when the command read the whole file and Codex did not truncate the output,
    so read_ledger.py fills the same ledger the coverage gates consult;
  * `spawn_agent` -> `Agent`; MCP names pass through;
  * events whose hooks read the transcript (UserPromptSubmit, PreToolUse, Stop)
    get `transcript_path` rewritten to a Claude-shaped conversion of the Codex
    rollout (transcript_io.py, cached on size and mtime);
  * UserPromptSubmit text is wrapped as JSON additionalContext, because Codex
    drops plain stdout from that event (verified 2026-09-07);
  * registry rows carry "agents"; rows listed for Claude only (the stage 5
    instruments and the Claude-transcript meter) never run for Codex.
Set CLAUDE_HOOK_DUMP_DIR to a folder to record every raw and translated payload
(the diagnostic fixture Codex asked for); never set it in a wired command.

Folding rules (what the host sees):
  * exit code 2 from any child -> exit 2, every child's stderr forwarded
    (a PreToolUse block); any other non-zero -> forwarded to stderr, exit 0
    (non-blocking, as before);
  * a Claude Stop bounce (exit 2 from a Stop child, 2026-10-31) -> exit 0 and
    {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext":
    <the children's stderr>}} on stdout: non-error hook feedback, so no hook
    error reaches the user's screen; a Codex block keeps its JSON block shape;
  * stdout: UserPromptSubmit concatenates every child's text (context lines);
    other events forward the one JSON object a child printed, or merge several
    (systemMessage joined, hookSpecificOutput.additionalContext joined, a
    "block" decision wins with reasons joined); plain text is concatenated.
  * CLAUDE_DISPATCH_SKIP="a.py,b.py,sync" skips named scripts (harness_metrics
    uses it so a probe never triggers the weekly self-test spawn).
  * a child that times out (60 s, Claude Code's own budget) is reported on
    stderr and treated as exit 0.

Fails open at the dispatcher level too: an internal error is appended to
~\.claude\state\hook_dispatch_errors.log and the exit code is 0, because a
dead dispatcher would disarm every gate at once, silently.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "hook_registry.json")
STATE_DIR = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "state")
CHILD_TIMEOUT = 60
# Windows console rules (2026-09-06, second round of flashing; revised
# 2026-09-07 after the owner saw the windows again):
#   * the dispatcher itself is wired as `pythonw`, which owns no console;
#   * children get CREATE_NO_WINDOW through proc_quiet.quiet_run, and so
#     does every grandchild launch in the hook family (git in auto_snapshot,
#     tasklist in mail_preflight, the validator, the weekly runner): a
#     console-less parent that launches a console program plainly gets a
#     VISIBLE window, so the rule holds at every level;
#   * the 2026-09-06 answer (a new console per child, hidden through the
#     show-window flag) failed on this machine because Windows Terminal,
#     the default console host, ignores that flag (microsoft/terminal 12464
#     and 15311). Verified by eye, not by the process list.
try:
    from proc_quiet import quiet_run
except ImportError:  # support file missing: never wedge the harness
    quiet_run = subprocess.run
try:
    import transcript_io
except Exception:  # pragma: no cover
    transcript_io = None
try:
    import patch_parse
except Exception:  # pragma: no cover
    patch_parse = None


def log_error(msg):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "hook_dispatch_errors.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    except Exception:
        pass


def load_registry(path=REGISTRY):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def matches(matcher, tool_name):
    if not matcher:
        return True
    if not tool_name:
        return False
    try:
        return re.search(matcher, tool_name) is not None
    except re.error:
        return matcher == tool_name


def read_stdin_utf8():
    """The host's payload, decoded as UTF-8 whatever the console code page.

    2026-10-13 (a joint Claude and Codex usage review): Claude's hooks
    get PYTHONIOENCODING=utf-8 from ~/.claude/settings.json "env"; Codex's
    config.toml sets it only for shell tools, so Codex hooks most likely ran
    without it (inferred, not measured). Without it sys.stdin decodes the
    payload as cp1252 and every emoji arrives as mojibake (reproduced). The
    reply gate log matched that: 31 Codex rows since 2026-10-08 21:03Z, 0
    passes, 15 of 16 bounced replies false positives.
    """
    stream = sys.stdin
    if stream is None:
        return ""
    buf = getattr(stream, "buffer", None)
    if buf is None:
        return stream.read()
    return buf.read().decode("utf-8", errors="replace")


def run_child(entry, payload, env):
    cmd = entry["cmd"]
    t0 = time.time()
    try:
        p = quiet_run(cmd, input=payload, capture_output=True, text=True, encoding="utf-8",
                      errors="replace", env=env, timeout=CHILD_TIMEOUT)
        return {"script": entry["script"], "rc": p.returncode, "out": p.stdout or "", "err": p.stderr or "",
                "secs": round(time.time() - t0, 2)}
    except subprocess.TimeoutExpired:
        return {"script": entry["script"], "rc": 0, "out": "",
                "err": "[hook_dispatch] %s timed out after %ds (treated as pass)\n" % (entry["script"], CHILD_TIMEOUT),
                "secs": CHILD_TIMEOUT}
    except Exception as exc:
        return {"script": entry["script"], "rc": 0, "out": "", "err": "[hook_dispatch] %s failed to start: %r\n" % (entry["script"], exc),
                "secs": round(time.time() - t0, 2)}


def parse_json(text):
    s = text.strip()
    if not s.startswith("{"):
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def merge_json(objs):
    out = {}
    for o in objs:
        for k, v in o.items():
            if k == "systemMessage":
                out[k] = (out.get(k, "") + (" | " if out.get(k) else "") + str(v))
            elif k == "hookSpecificOutput" and isinstance(v, dict):
                cur = out.setdefault(k, {})
                for kk, vv in v.items():
                    if kk == "additionalContext":
                        cur[kk] = (cur.get(kk, "") + ("\n" if cur.get(kk) else "") + str(vv))
                    else:
                        cur.setdefault(kk, vv)
            elif k == "decision":
                if v == "block" or "decision" not in out:
                    out[k] = v
            elif k == "reason":
                out[k] = (out.get(k, "") + ("\n" if out.get(k) else "") + str(v))
            else:
                out.setdefault(k, v)
    return out


def fold(event, results):
    """Return (stdout_text, stderr_text, exit_code)."""
    err = "".join(r["err"] for r in results if r["err"])
    code = 2 if any(r["rc"] == 2 for r in results) else 0
    outs = [r["out"] for r in results if r["out"].strip()]
    if event == "UserPromptSubmit":
        return ("\n".join(o.rstrip("\n") for o in outs) + ("\n" if outs else ""), err, code)
    jsons = [j for j in (parse_json(o) for o in outs) if isinstance(j, dict)]
    if jsons:
        obj = jsons[0] if len(jsons) == 1 else merge_json(jsons)
        return (json.dumps(obj, ensure_ascii=False), err, code)
    return ("\n".join(o.rstrip("\n") for o in outs) + ("\n" if outs else ""), err, code)


def sync_claude_md(reg):
    s = reg.get("sync") or {}
    src, dst = s.get("src"), s.get("dst")
    if src and dst and os.path.isfile(src):
        try:
            shutil.copyfile(src, dst)
        except OSError as exc:
            log_error("sync failed: %r" % (exc,))
        sync_codex_agents_md(src)


def sync_codex_agents_md(master):
    """Codex integration part 1 (2026-09-07): keep ~/.codex/AGENTS.md a
    function of the same master. Regenerates only when the master, the delta
    or the home CLAUDE.md (hook table) is newer than the current AGENTS.md, so
    a prompt costs one stat, not a process. build_agents_md.py fails open."""
    builder = os.path.join(HERE, "build_agents_md.py")
    if not os.path.isfile(builder):
        return
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    out = os.path.join(codex_home, "AGENTS.md")
    # the Codex-only delta file sits beside the master
    delta = os.path.join(os.path.dirname(master), "CODEX_DELTA.md")
    home_md = os.path.join(os.path.expanduser("~"), "CLAUDE.md")
    try:
        newest = max(os.path.getmtime(p) for p in (master, delta, home_md, builder) if os.path.isfile(p))
        if os.path.isfile(out) and os.path.getmtime(out) >= newest:
            return
        quiet_run([sys.executable, builder], capture_output=True, text=True, timeout=30)
    except Exception as exc:
        log_error("codex AGENTS.md sync failed: %r" % (exc,))


def agent_of(data, argv):
    """Which host is calling: 'claude' or 'codex' (Codex integration, 2026-09-07).
    An explicit --agent wins; otherwise the transcript path (Codex rollouts live under
    .codex\\sessions), then a Codex-only payload field, then the CLAUDE_HOOK_AGENT env."""
    if "--agent" in argv:
        try:
            return argv[argv.index("--agent") + 1].strip().lower()
        except IndexError:
            pass
    tp = str(data.get("transcript_path") or "").replace("/", "\\").lower()
    if "\\.codex\\" in tp:
        return "codex"
    if "\\.claude\\" in tp:
        return "claude"
    if str(data.get("model") or "").lower().startswith(("gpt", "codex", "o1", "o3", "o4")):
        return "codex"
    return (os.environ.get("CLAUDE_HOOK_AGENT") or "claude").strip().lower()


def codex_block_output(event, reason):
    """A block in the JSON shapes Codex documents: modern permissionDecision for
    PreToolUse plus the legacy decision/reason pair (Stop and PostToolUse use the
    latter). Exit code 0 carries it; exit 2 alone was ignored (2026-09-07)."""
    obj = {"decision": "block", "reason": reason}
    if event == "PreToolUse":
        obj["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": reason}
    return json.dumps(obj, ensure_ascii=False)


def claude_stop_feedback(out, err):
    """A Claude Stop bounce as non-error feedback: {"hookSpecificOutput": {"hookEventName": "Stop",
    "additionalContext": <the children's stderr, joined as before>}}. A JSON object a child already printed
    is kept and its additionalContext extended; plain child stdout is appended to the feedback text."""
    obj = parse_json(out) if out.strip() else None
    text = err.strip()
    if not isinstance(obj, dict):
        if out.strip():
            text = (text + "\n" + out.strip()).strip()
        obj = {}
    hso = obj.get("hookSpecificOutput") if isinstance(obj.get("hookSpecificOutput"), dict) else {}
    prev = str(hso.get("additionalContext") or "").strip()
    hso["hookEventName"] = "Stop"
    hso["additionalContext"] = (prev + "\n" + text).strip() if prev else (text or "blocked by a Stop hook")
    obj["hookSpecificOutput"] = hso
    return json.dumps(obj, ensure_ascii=False)


def codex_prompt_output(text):
    """Codex ignores plain stdout from a UserPromptSubmit hook (verified 2026-09-07:
    the prompt hooks wrote their state flag but no contract text reached the model,
    in exec and in the desktop session), so the concatenated text is wrapped in the
    JSON shape both hosts accept."""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                              "additionalContext": text}}, ensure_ascii=False)


# ---------------------------------------------------------------- Codex translation
TRUNC_RE = re.compile(r"truncated output|output truncated|\[\.\.\. *\d+ (?:bytes|lines|chars) truncated", re.I)
READ_VERB = re.compile(r"(?:^|[;|&(\s])(?P<verb>Get-Content|gc|cat|type|sed|head|tail|less|more)\b(?P<args>[^|;&\n]*)", re.I)
PARTIAL_FLAG = re.compile(r"-(?:TotalCount|Tail|Head|First|Last)\b", re.I)
SELECT_PARTIAL = re.compile(r"\|\s*(?:Select-Object|select)\s+[^|;]*-(?:First|Last|Skip)\b", re.I)
PATH_TOKEN = re.compile(r"""'([^']+)'|"([^"]+)"|(\S+)""")


PASS_THROUGH = {"out-string", "out-host", "out-default", "write-output", "write-host", "cat", "more", "less", "tee",
                "format-table", "format-list", "ft", "fl", "oh", "type"}
FAIL_TEXT = re.compile(r"Access (?:is )?denied|No such file|cannot find path|ObjectNotFound|PermissionError|"
                       r"FileNotFoundError|not recognized as|CommandNotFound", re.I)


def response_facts(resp):
    """(ok, output_text, truncated) from a Codex tool_response of any shape: a dict with
    exit_code/output, a string, or None (unknown). ok is None when nothing says."""
    if resp is None:
        return None, None, False
    text = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
    truncated = bool(TRUNC_RE.search(text)) or bool(re.search(r'"truncated"\s*:\s*true', text, re.I))
    ok = None
    if isinstance(resp, dict):
        for k in ("exit_code", "exitCode", "returncode", "status_code"):
            if isinstance(resp.get(k), (int, float)):
                ok = int(resp[k]) == 0
                break
        if ok is None and isinstance(resp.get("truncated"), bool):
            truncated = truncated or resp["truncated"]
    if ok is None:
        m = re.search(r'"(?:exit_code|exitCode)"\s*:\s*(-?\d+)', text)
        if m:
            ok = int(m.group(1)) == 0
    if ok is None and FAIL_TEXT.search(text):
        ok = False
    out = None
    if isinstance(resp, dict):
        for k in ("output", "stdout", "text", "content", "result"):
            if isinstance(resp.get(k), str):
                out = resp[k]
                break
        if out is None:
            m = re.search(r'"output"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            out = json.loads('"' + m.group(1) + '"') if m else text
    else:
        out = text
    return ok, out, truncated


def read_targets(cmd, cwd):
    """[(path, partial_reason_or_empty)] for the files a shell command reads. A read whose
    output is piped into anything but a display command, or redirected, did not reach
    the model whole (Codex review 2026-09-07: Get-Content ... | Out-Null)."""
    found = []
    select_partial = bool(SELECT_PARTIAL.search(cmd or ""))
    for m in READ_VERB.finditer(cmd or ""):
        verb = m.group("verb").lower()
        args = m.group("args") or ""
        reason = ""
        if verb in ("sed", "head", "tail") or PARTIAL_FLAG.search(args) or select_partial:
            reason = "ranged read"
        after = (cmd or "")[m.end():].lstrip()
        if after.startswith("|"):
            nxt = after[1:].strip().split()
            if not nxt or nxt[0].lower() not in PASS_THROUGH:
                reason = reason or "piped into %s" % (nxt[0] if nxt else "?")
        if ">" in args:
            reason = reason or "redirected"
        tokens = []
        for q1, q2, bare in PATH_TOKEN.findall(args):
            tok = q1 or q2 or bare
            if not tok or tok.startswith("-") or tok in ("-n", "-e"):
                continue
            if verb == "sed" and (tok.endswith("p") and ("," in tok or tok[:-1].isdigit())):
                continue
            for part in tok.split(","):
                part = part.strip().strip("'\"")
                if part and not part.startswith("-") and part not in (">", ">>"):
                    tokens.append(part)
        for tok in tokens:
            p = tok if os.path.isabs(tok) else os.path.join(cwd or os.getcwd(), tok)
            try:
                if os.path.isfile(p):
                    found.append((os.path.normpath(p), reason))
            except OSError:
                continue
    return found


def read_payloads(base, cmds, tool_response):
    """Synthesized Read payloads for a Codex PostToolUse shell call. Credit rules (Codex
    acceptance, 2026-09-07): a failed command credits nothing; a successful command
    with empty output credits nothing (discarded or transformed); truncated output,
    a ranged read, a piped or redirected read, or unknown output is partial; only a
    whole-file read with visible, complete output is full (read_ledger adds the hash)."""
    ok, out_text, truncated = response_facts(tool_response)
    if ok is False:
        return []
    out = []
    seen = set()
    for cmd in cmds:
        for path, reason in read_targets(cmd, base.get("cwd")):
            if out_text is not None and not out_text.strip():
                try:
                    if os.path.getsize(path) > 0:
                        continue  # the file has content, the model saw none
                except OSError:
                    continue
            why = reason or ("truncated output" if truncated else "") or ("no output captured" if out_text is None else "")
            key = (path.lower(), why)
            if key in seen:
                continue
            seen.add(key)
            p = dict(base)
            p["tool_name"] = "Read"
            ti = {"file_path": path, "_codex_shell_read": True}
            if why:
                ti["limit"] = 1
                ti["_codex_partial_reason"] = why
            p["tool_input"] = ti
            out.append(p)
    return out


def edit_payloads(base, ops):
    out = []
    for op in ops:
        flags = {"_codex_op": op["op"], "_codex_applied": bool(op.get("applied")), "_codex_error": op.get("error", "")}
        p = dict(base)
        if op["op"] == "add":
            p["tool_name"] = "Write"
            p["tool_input"] = dict(flags, file_path=op["path"], content=op.get("content") or "")
        elif op["op"] in ("update", "move"):
            old, new = patch_parse.edit_strings(op)
            p["tool_name"] = "Edit"
            ti = dict(flags, file_path=op["path"], old_string=old, new_string=new)
            if op.get("after") is not None:
                ti["_codex_after"] = op["after"]
            if op["op"] == "move" and op.get("new_path"):
                ti["_codex_move_to"] = op["new_path"]
                q = dict(base)
                q["tool_name"] = "Write"
                q["tool_input"] = dict(flags, file_path=op["new_path"], content=op.get("after") if op.get("after") is not None else new)
                out.append(q)
            p["tool_input"] = ti
        else:
            p["tool_name"] = "Write"
            p["tool_input"] = dict(flags, file_path=op["path"], content="")
        out.append(p)
    return out


def translate(event, data, agent):
    """The list of Claude-shaped payloads to dispatch for this call."""
    if agent != "codex":
        return [data]
    d = dict(data)
    if transcript_io is not None and event in ("UserPromptSubmit", "PreToolUse", "Stop"):
        try:
            conv = transcript_io.ensure_claude_transcript(d)
            if conv:
                d["_codex_rollout"] = d.get("transcript_path")
                d["transcript_path"] = conv
        except Exception as exc:
            log_error("transcript conversion failed: %r" % (exc,))
    if event not in ("PreToolUse", "PostToolUse"):
        return [d]
    tool = str(d.get("tool_name") or "")
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    d["_codex_tool"] = tool
    out = []
    cmds = transcript_io.shell_commands(tool, ti) if transcript_io is not None else None
    if cmds is not None:
        for cmd in cmds:
            p = dict(d)
            p["tool_name"] = "Bash"
            p["tool_input"] = {"command": cmd, "_codex_tool": tool}
            out.append(p)
        if event == "PostToolUse":
            out.extend(read_payloads(d, cmds, d.get("tool_response")))
    # patches: a direct apply_patch call, or apply_patch(...) nested in code-mode
    # JavaScript (Codex review 2026-09-07: a real calling shape); every patch, every file
    if transcript_io is not None and patch_parse is not None:
        for text in transcript_io.patch_texts(tool, ti):
            try:
                ops = patch_parse.parse_and_apply(text, d.get("cwd"))
            except Exception as exc:
                log_error("patch parse failed: %r" % (exc,))
                ops = []
            out.extend(edit_payloads(d, ops))
    if out:
        return out
    if tool in ("spawn_agent", "collaboration.spawn_agent") or tool.endswith(".spawn_agent"):
        d["tool_name"] = "Agent"
        return [d]
    return [d]


def dump_payloads(event, argv, data, translated):
    ddir = os.environ.get("CLAUDE_HOOK_DUMP_DIR")
    if not ddir:
        return
    try:
        os.makedirs(ddir, exist_ok=True)
        fn = os.path.join(ddir, "%s_%s_%d.json" % (event, time.strftime("%H%M%S"), os.getpid()))
        with open(fn, "w", encoding="utf-8") as f:
            json.dump({"argv": argv, "payload": data, "translated": translated}, f, ensure_ascii=False, indent=1)
    except Exception as exc:
        log_error("dump failed: %r" % (exc,))


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    event = argv[0] if argv else ""
    # lstrip: a PowerShell pipe prepends a UTF-8 BOM (2026-09-07 Codex wiring test);
    # a BOM would make every payload {} and silently unmatch every tool matcher.
    payload = read_stdin_utf8().lstrip("﻿")
    try:
        data = json.loads(payload or "{}")
    except Exception:
        data = {}
    reg = load_registry()
    skip = {s.strip() for s in os.environ.get("CLAUDE_DISPATCH_SKIP", "").split(",") if s.strip()}
    if event == "UserPromptSubmit" and "sync" not in skip:
        sync_claude_md(reg)
    agent = agent_of(data, argv)
    payloads = translate(event, data, agent)
    dump_payloads(event, argv, data, payloads)
    env = dict(os.environ)
    env.pop("CLAUDE_TOOL_INPUT", None)
    # children read the payload this process writes as UTF-8 (run_child); without this
    # their stdin decodes as cp1252 under Codex (see read_stdin_utf8)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLAUDE_HOOK_CHILD"] = "1"  # children know they run console-less under the dispatcher
    env["CLAUDE_HOOK_AGENT"] = agent  # 'claude' or 'codex'; hooks may adapt
    jobs = []
    for p in payloads:
        tool = p.get("tool_name") or ""
        for e in reg.get("events", {}).get(event, []):
            if e["script"] in skip or not matches(e.get("matcher", ""), tool):
                continue
            if agent not in (e.get("agents") or ["claude", "codex"]):
                continue
            jobs.append((e, json.dumps(p, ensure_ascii=False) if p is not data else payload))
    if not jobs:
        return 0
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
        results = list(ex.map(lambda j: run_child(j[0], j[1], env), jobs))
    out, err, code = fold(event, results)
    if out and event == "UserPromptSubmit" and agent == "codex" and not out.lstrip().startswith("{"):
        out = codex_prompt_output(out.rstrip("\n"))
    if agent == "codex" and code == 2 and event in ("PreToolUse", "PostToolUse", "Stop"):
        # Codex did not act on exit 2 + stderr in the 2026-09-07 live runs (heredoc,
        # comms and discuss gates all returned 2 and the tool ran), so a block is
        # spelled out in the JSON forms its docs list; the text stays on stderr too.
        out, code = codex_block_output(event, err.strip() or out.strip() or "blocked by a hook"), 0
    if agent == "claude" and code == 2 and event == "Stop":
        # 2026-10-31 (the owner: a hook error on screen "confuses me and has negative value"): a Stop
        # bounce goes back as hookSpecificOutput.additionalContext, which the Claude Code hooks reference
        # documents as non-error feedback ("shown in the transcript as hook feedback rather than a hook error",
        # same stop_hook_active protection). Nothing goes to stderr; PreToolUse and Codex keep their shapes.
        out, err, code = claude_stop_feedback(out, err), "", 0
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    sys.stdout.flush()
    sys.stderr.flush()
    return code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as exc:
        log_error("dispatcher error: %r" % (exc,))
        sys.exit(0)
