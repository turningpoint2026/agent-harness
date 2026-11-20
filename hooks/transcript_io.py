#!/usr/bin/env python3
r"""transcript_io.py - one transcript shape for both hosts.

Codex integration Part 2 (2026-09-07). Seven hooks read the session
transcript in Claude Code's jsonl shape (response_length_log, turn_end_gate,
session_memory_writeback, response_style_contract, session_mode,
discuss_mode_gate, writing_format_preflight). Codex writes a different
rollout jsonl. Rather than teach seven parsers a second format, the
dispatcher converts a Codex rollout into a Claude-shaped transcript under
~\.claude\state\codex_transcripts\<session_id>.jsonl and rewrites
`transcript_path` in the payload it hands to the children. The conversion is
cached on the rollout's (size, mtime) so a quiet prompt costs one stat.

Mapping (rollout `response_item` payloads):
  message/user       -> {"type":"user","message":{"role":"user","content":text}}
                        (a real prompt in the hooks' terms: plain string content)
  message/developer  -> {"type":"user","isMeta":true,"message":{"content":[{"type":"text",...}]}}
  message/assistant  -> {"type":"assistant","message":{"content":[{"type":"text","text":...}]}}
  function_call, custom_tool_call
                     -> assistant tool_use block with the Claude tool name:
                        exec_command/shell/Bash -> Bash {command}; the code-mode
                        `exec` tool (JavaScript calling tools.exec_command) ->
                        Bash with the extracted command(s); apply_patch -> Edit
                        {file_path: first path, patch}; spawn_agent -> Agent
  *_output           -> user tool_result block (list content: not a prompt)
  reasoning, world_state, session_meta -> dropped
The Stop payload's `last_assistant_message` is appended as a final assistant
entry when the rollout has not flushed it yet.
"""
import json
import os
import re
import sys

STATE_DIR = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "state")
JS_CMD = re.compile(r"""exec_command\s*\(\s*\{[^{}]*?\bcmd\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)""", re.S)
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", re.M)
SHELL_TOOLS = {"bash", "exec_command", "shell", "local_shell", "container.exec", "powershell", "functions.exec_command"}


def _js_string(lit):
    """A JavaScript string literal -> its value (double quotes via json, others by hand)."""
    q = lit[0]
    body = lit[1:-1]
    if q == '"':
        try:
            return json.loads(lit)
        except Exception:
            pass
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), body)


def commands_from_js(code):
    """Every tools.exec_command({cmd: "..."}) command in a code-mode script."""
    return [_js_string(m.group(1)) for m in JS_CMD.finditer(code or "")]


JS_PATCH = re.compile(r"""apply_patch\s*\(\s*(?:\{[^{}]*?\b(?:patch|input|command|text)\s*:\s*)?("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)""", re.S)


def patches_from_js(code):
    """Every apply_patch(...) patch text in a code-mode script (Codex nests edits in
    JavaScript too; found in its own session 2026-09-07)."""
    out = []
    for m in JS_PATCH.finditer(code or ""):
        text = _js_string(m.group(1))
        if "*** Begin Patch" in text:
            out.append(text)
    return out


def code_of(tool_name, tool_input):
    """The JavaScript of a code-mode `exec` call, else None."""
    t = str(tool_name or "").lower()
    ti = tool_input if isinstance(tool_input, dict) else {}
    if t == "exec" or t.endswith(".exec"):
        code = ti.get("input") or ti.get("code") or ti.get("command") or ""
        if isinstance(code, str):
            return code
    return None


def patch_texts(tool_name, tool_input):
    """[patch texts] carried by an apply_patch call or nested in code-mode JavaScript."""
    t = str(tool_name or "")
    ti = tool_input if isinstance(tool_input, dict) else {}
    if t == "apply_patch" or t.endswith(".apply_patch"):
        text = ti.get("command") or ti.get("patch") or ti.get("input") or ""
        return [text] if isinstance(text, str) and "*** Begin Patch" in text else []
    code = code_of(tool_name, tool_input)
    return patches_from_js(code) if code is not None else []


def shell_commands(tool_name, tool_input):
    """[commands] when the call is a shell execution in any of Codex's shapes, else None.
    A code-mode script yields its exec_command commands; a script that only patches
    yields None (the patch path handles it); a script with neither yields itself."""
    t = str(tool_name or "").lower()
    ti = tool_input if isinstance(tool_input, dict) else {}
    if t in SHELL_TOOLS:
        cmd = ti.get("command") or ti.get("cmd")
        if not cmd and isinstance(ti.get("args") or ti.get("argv"), list):
            cmd = " ".join(str(x) for x in (ti.get("args") or ti.get("argv")))
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        return [str(cmd or "")]
    code = code_of(tool_name, tool_input)
    if code is not None:
        found = commands_from_js(code)
        if found:
            return found
        if patches_from_js(code):
            return None
        return [code]
    return None


def claude_tool_uses(name, arguments):
    """[(claude_name, input_dict)] for a Codex tool call: a code-mode script can carry
    shell commands AND patches, so the answer is a list."""
    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {"input": args}
    if not isinstance(args, dict):
        args = {"input": args}
    out = []
    cmds = shell_commands(name, args)
    if cmds is not None:
        out.append(("Bash", {"command": "\n".join(cmds)}))
    for text in patch_texts(name, args):
        paths = PATCH_PATH.findall(text)
        out.append(("Edit", {"file_path": paths[0] if paths else "", "paths": paths, "patch": text}))
    if out:
        return out
    n = str(name or "")
    if n in ("spawn_agent", "collaboration.spawn_agent") or n.endswith(".spawn_agent"):
        return [("Agent", args)]
    return [(n, args)]


def claude_tool_use(name, arguments):
    """The first mapping (kept for callers that expect one)."""
    return claude_tool_uses(name, arguments)[0]


def _text_of(content):
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"):
            out.append(c.get("text", ""))
        elif isinstance(c, str):
            out.append(c)
    return "\n".join(out)


def convert_rollout(src_path, last_assistant_message=None):
    """Claude-shaped jsonl lines (as dicts) for a Codex rollout."""
    out = []
    last_assistant = None
    try:
        fh = open(src_path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "response_item":
                continue
            p = d.get("payload") or {}
            ts = d.get("timestamp")
            pt = p.get("type")
            if pt == "message":
                role = p.get("role")
                text = _text_of(p.get("content"))
                if role == "user":
                    out.append({"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}})
                elif role == "assistant":
                    last_assistant = text
                    out.append({"type": "assistant", "timestamp": ts,
                                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
                else:
                    out.append({"type": "user", "isMeta": True, "timestamp": ts,
                                "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
            elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
                name = p.get("name") or pt
                args = p.get("arguments") if "arguments" in p else p.get("input")
                if pt == "local_shell_call":
                    action = p.get("action") or {}
                    name, args = "exec_command", {"command": " ".join(action.get("command") or [])}
                blocks = [{"type": "tool_use", "id": p.get("call_id") or p.get("id"), "name": cname, "input": cinput}
                          for cname, cinput in claude_tool_uses(name, args)]
                out.append({"type": "assistant", "timestamp": ts,
                            "message": {"role": "assistant", "content": blocks}})
            elif pt in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
                res = p.get("output")
                out.append({"type": "user", "timestamp": ts,
                            "message": {"role": "user", "content": [
                                {"type": "tool_result", "tool_use_id": p.get("call_id"), "content": _text_of(res) if not isinstance(res, str) else res}]}})
    if last_assistant_message and (last_assistant or "").strip() != last_assistant_message.strip():
        out.append({"type": "assistant", "message": {"role": "assistant",
                                                     "content": [{"type": "text", "text": last_assistant_message}]}})
    return out


def converted_path(session_id):
    d = os.path.join(STATE_DIR, "codex_transcripts")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id or "nosession"))[:80]
    return os.path.join(d, safe + ".jsonl")


def ensure_claude_transcript(payload):
    """For a Codex payload: convert its rollout (cached) and return the converted path, else None."""
    src = payload.get("transcript_path") or ""
    sid = payload.get("session_id") or "nosession"
    lam = payload.get("last_assistant_message")
    if not src or not os.path.isfile(src):
        return None
    dst = converted_path(sid)
    meta = dst + ".meta.json"
    try:
        import hashlib
        st = os.stat(src)
        # the whole reply is in the key: two replies with the same first 200 chars
        # and different tails reused the first conversion (Codex review, 2026-09-07)
        key = {"size": st.st_size, "mtime_ns": getattr(st, "st_mtime_ns", st.st_mtime),
               "lam_sha256": hashlib.sha256((lam or "").encode("utf-8")).hexdigest()}
        if os.path.isfile(dst) and os.path.isfile(meta):
            if json.load(open(meta, encoding="utf-8")) == key:
                return dst
        rows = convert_rollout(src, lam)
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, dst)
        json.dump(key, open(meta, "w", encoding="utf-8"))
        return dst
    except Exception:
        return None


def last_reply(payload):
    """The final reply text for a Stop payload from either host."""
    lam = payload.get("last_assistant_message")
    if isinstance(lam, str) and lam.strip():
        return lam
    path = payload.get("transcript_path") or ""
    text = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "assistant":
                    c = (d.get("message") or {}).get("content")
                    if isinstance(c, list):
                        t = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                        if t.strip():
                            text = t
    except OSError:
        pass
    return text


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1:
        for r in convert_rollout(sys.argv[1]):
            print(json.dumps(r, ensure_ascii=False)[:300])
