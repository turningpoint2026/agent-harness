"""hook_dispatch.py: one process per hook event, result folding, and the Codex payload translation."""
import json
import os
import shutil
import subprocess
import sys

from conftest import HOOKS, base_env, load_module

DISP = os.path.join(HOOKS, "hook_dispatch.py")


def make_dispatch_dir(tmp, name, helpers=()):
    """The dispatcher reads hook_registry.json beside ITSELF, so each test runs a copy from a temp dir."""
    d = os.path.join(tmp, name)
    os.makedirs(d)
    shutil.copyfile(DISP, os.path.join(d, "hook_dispatch.py"))
    for m in helpers:
        shutil.copyfile(os.path.join(HOOKS, m), os.path.join(d, m))
    return d


def stub(d, name, body):
    p = os.path.join(d, name)
    open(p, "w").write(body)
    return p


def disp(d, event, payload, state, **extra):
    env = base_env(state, **extra)
    p = subprocess.run([sys.executable, os.path.join(d, "hook_dispatch.py"), event], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    return p.returncode, p.stdout, p.stderr


def test_dispatch_folding(tmp_path, state, check):
    dd = make_dispatch_dir(str(tmp_path), "dispatch")
    a = stub(dd, "a.py", "import sys; sys.stderr.write('BLOCK A\\n'); sys.exit(2)\n")
    b = stub(dd, "b.py", "import sys; print('context from B')\n")
    c = stub(dd, "c.py", "import json; print(json.dumps({'systemMessage': 'warn C'}))\n")
    d_ = stub(dd, "d.py", "import json; print(json.dumps({'systemMessage': 'warn D', 'hookSpecificOutput': {'additionalContext': 'ctx D'}}))\n")
    src_md = stub(dd, "master.md", "MASTER v1\n")
    dst_md = stub(dd, "loaded.md", "old\n")
    reg = {"events": {
        "PreToolUse": [{"matcher": "Edit|Write", "script": "a.py", "cmd": [sys.executable, a]},
                       {"matcher": "", "script": "b.py", "cmd": [sys.executable, b]},
                       {"matcher": "Bash", "script": "c.py", "cmd": [sys.executable, c]},
                       {"matcher": "Bash", "script": "d.py", "cmd": [sys.executable, d_]}],
        "UserPromptSubmit": [{"matcher": "", "script": "b.py", "cmd": [sys.executable, b]},
                             {"matcher": "", "script": "c.py", "cmd": [sys.executable, c]}],
    }, "sync": {"src": src_md, "dst": dst_md}}
    json.dump(reg, open(os.path.join(dd, "hook_registry.json"), "w"))

    rc, o, e = disp(dd, "PreToolUse", {"tool_name": "Write", "tool_input": {}}, state)
    check("a matching exit-2 child blocks the event (stderr forwarded) while the unmatched Bash hooks stay silent",
          rc == 2 and "BLOCK A" in e and "context from B" in o and "warn C" not in o, f"rc={rc} o={o[:80]!r} e={e[:80]!r}")
    rc, o, e = disp(dd, "PreToolUse", {"tool_name": "Bash", "tool_input": {}}, state)
    try:
        jo = json.loads(o.strip().splitlines()[-1])
    except Exception:
        jo = {}
    check("two JSON children merge (systemMessage joined, additionalContext kept); the exit-2 hook is filtered by matcher",
          rc == 0 and jo.get("systemMessage") == "warn C | warn D" and jo.get("hookSpecificOutput", {}).get("additionalContext") == "ctx D" and "BLOCK" not in e)
    rc, o, e = disp(dd, "UserPromptSubmit", {"prompt": "hi"}, state)
    check("UserPromptSubmit concatenates child text and performs the rule-file sync copy",
          rc == 0 and "context from B" in o and "warn C" in o and open(dst_md).read() == "MASTER v1\n")
    open(dst_md, "w").write("old again\n")
    rc, o, e = disp(dd, "UserPromptSubmit", {"prompt": "hi"}, state, CLAUDE_DISPATCH_SKIP="sync,c.py")
    check("CLAUDE_DISPATCH_SKIP skips the named script and the sync",
          rc == 0 and "context from B" in o and "warn C" not in o and open(dst_md).read() == "old again\n")
    rc, o, e = disp(dd, "UserPromptSubmit", {"prompt": "hi", "session_id": "cx1", "transcript_path": "C:\\Users\\x\\.codex\\sessions\\2026\\r.jsonl"}, state)
    try:
        cxj = json.loads(o)
    except Exception:
        cxj = {}
    check("a Codex prompt payload gets {hookSpecificOutput.additionalContext} carrying the children's text (Codex drops plain stdout)",
          rc == 0 and "context from B" in str(cxj.get("hookSpecificOutput", {}).get("additionalContext", "")))
    rc, o, e = disp(dd, "UserPromptSubmit", {"prompt": "hi", "session_id": "cl1", "model": "gpt-6-astra"}, state)
    check("a payload carrying a gpt model name counts as Codex too", rc == 0 and o.lstrip().startswith("{") and "context from B" in o)
    rc, o, e = disp(dd, "UserPromptSubmit", {"prompt": "hi", "session_id": "cl2", "transcript_path": "C:\\Users\\x\\.claude\\projects\\p\\s.jsonl"}, state)
    check("a Claude prompt payload keeps plain concatenated text", rc == 0 and not o.lstrip().startswith("{") and "context from B" in o)
    rc, o, e = disp(dd, "Stop", {"stop_hook_active": False}, state)
    check("an event with no registered hooks exits 0 silently", rc == 0 and o.strip() == "" and e.strip() == "")

    # 2026-10-13: Codex starts hooks with no PYTHONIOENCODING; the payload must still arrive as UTF-8
    du = make_dispatch_dir(str(tmp_path), "dispatch_utf8")
    se = stub(du, "e.py", "import sys, json\nd = json.loads(sys.stdin.read())\nprint('EMOJI-OK' if d.get('tool_input', {}).get('note') == '\\U0001f7e8' else 'EMOJI-BAD')\n")
    json.dump({"events": {"PostToolUse": [{"matcher": "", "script": "e.py", "cmd": [sys.executable, se]}]}}, open(os.path.join(du, "hook_registry.json"), "w"))
    env = base_env(state)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    pu = subprocess.run([sys.executable, os.path.join(du, "hook_dispatch.py"), "PostToolUse"],
                        input=json.dumps({"tool_name": "Write", "tool_input": {"note": "\U0001f7e8"}}, ensure_ascii=False).encode("utf-8"),
                        capture_output=True, env=env, timeout=120)
    check("with no PYTHONIOENCODING a raw UTF-8 emoji reaches the child intact", pu.returncode == 0 and "EMOJI-OK" in pu.stdout.decode("utf-8", "replace"))
    check.done()


def test_stop_bounce_is_feedback_not_error(tmp_path, state, check):
    """2026-10-31: a Claude Stop bounce leaves the dispatcher as exit 0 plus additionalContext (non-error hook
    feedback); PreToolUse still exits 2; Codex gets its documented block JSON."""
    dy = make_dispatch_dir(str(tmp_path), "dispatch_stop_fb")
    y1 = stub(dy, "y1.py", "import sys; sys.stderr.write('[REPLY FRAME] stub bounce\\n'); sys.exit(2)\n")
    y2 = stub(dy, "y2.py", "import sys; sys.stderr.write('[TURN END GATE R3] stub claim\\n'); sys.exit(2)\n")
    json.dump({"events": {"Stop": [{"matcher": "", "script": "y1.py", "cmd": [sys.executable, y1]},
                                   {"matcher": "", "script": "y2.py", "cmd": [sys.executable, y2]}],
                          "PreToolUse": [{"matcher": "", "script": "y1.py", "cmd": [sys.executable, y1]}]}},
              open(os.path.join(dy, "hook_registry.json"), "w"))

    def run(event, agent=None):
        pl = {"session_id": "rf-y", "cwd": str(tmp_path), "hook_event_name": event, "stop_hook_active": False}
        if event == "PreToolUse":
            pl.update(tool_name="Bash", tool_input={"command": "ls"})
        rc, out, err = disp(dy, event, pl, state, CLAUDE_HOOK_AGENT=agent)
        try:
            o = json.loads(out.strip()) if out.strip() else {}
        except ValueError:
            o = {"_raw": out}
        return rc, o, err
    rc1, o1, e1 = run("Stop")
    ctx = str((o1.get("hookSpecificOutput") or {}).get("additionalContext", ""))
    rc2, _, e2 = run("PreToolUse")
    rc3, o3, _ = run("Stop", agent="codex")
    check("Claude Stop: rc 0 plus additionalContext carrying every Stop child's message, nothing on stderr",
          rc1 == 0 and (o1.get("hookSpecificOutput") or {}).get("hookEventName") == "Stop" and "[REPLY FRAME] stub bounce" in ctx
          and "[TURN END GATE R3] stub claim" in ctx and not e1.strip() and "decision" not in o1)
    check("PreToolUse still exits 2 with the text on stderr", rc2 == 2 and "[REPLY FRAME] stub bounce" in e2)
    check("Codex Stop keeps the block JSON", rc3 == 0 and o3.get("decision") == "block" and "stub bounce" in str(o3.get("reason", "")))
    check.done()


def test_codex_translation(tmp_path, state, check):
    tmp = str(tmp_path)
    dd = make_dispatch_dir(tmp, "dispatch", helpers=("proc_quiet.py", "transcript_io.py", "patch_parse.py", "read_ledger.py"))
    pp = load_module("patch_parse_t", os.path.join(HOOKS, "patch_parse.py"))
    tio = load_module("transcript_io_t", os.path.join(HOOKS, "transcript_io.py"))
    cxd = os.path.join(tmp, "cx_patch")
    os.makedirs(cxd)
    upd = os.path.join(cxd, "notes.md")
    open(upd, "w", encoding="utf-8").write("alpha\nbeta\ngamma\ndelta\n")

    # patch_parse
    ops = pp.parse_and_apply("*** Begin Patch\n*** Add File: new_doc.md\n+first line\n+second line\n"
                             "*** Update File: notes.md\n@@\n alpha\n-beta\n+BETA changed\n gamma\n*** Delete File: gone.md\n*** End Patch\n", cxd)
    check("patch_parse: add/update/delete parsed with paths resolved against cwd",
          [o["op"] for o in ops] == ["add", "update", "delete"] and ops[0]["path"] == os.path.normpath(os.path.join(cxd, "new_doc.md"))
          and ops[0]["content"] == "first line\nsecond line\n")
    check("patch_parse: an update reconstructs the whole file (before and after) and applies",
          ops[1]["applied"] and ops[1]["before"] == "alpha\nbeta\ngamma\ndelta\n" and ops[1]["after"] == "alpha\nBETA changed\ngamma\ndelta\n")
    check("patch_parse: edit_strings gives the changed region only", pp.edit_strings(ops[1]) == ("alpha\nbeta\ngamma", "alpha\nBETA changed\ngamma"))
    check("patch_parse: a delete of a missing file is reported, not guessed", ops[2]["applied"] is False and "does not exist" in ops[2]["error"])
    bad = pp.parse_and_apply("*** Begin Patch\n*** Update File: notes.md\n@@\n-nonexistent line\n+x\n*** End Patch\n", cxd)
    check("patch_parse: a hunk that does not match reports an error and applied=False", bad[0]["applied"] is False and "does not match" in bad[0]["error"])
    mv = pp.parse_and_apply("*** Begin Patch\n*** Update File: notes.md\n*** Move to: renamed.md\n@@\n delta\n+epsilon\n*** End Patch\n", cxd)
    check("patch_parse: Move to becomes a move op with new_path and the after content",
          mv[0]["op"] == "move" and mv[0]["new_path"].endswith("renamed.md") and mv[0]["after"].endswith("delta\nepsilon\n"))
    eoff = os.path.join(cxd, "eof.txt")
    open(eoff, "w", encoding="utf-8").write("same\nmiddle\nsame\n")
    eo = pp.parse_and_apply("*** Begin Patch\n*** Update File: eof.txt\n@@\n-same\n+changed\n*** End of File\n*** End Patch\n", cxd)
    check("patch_parse: an End of File hunk changes the LAST occurrence of a repeated line", eo[0]["applied"] and eo[0]["after"] == "same\nmiddle\nchanged\n")
    apf = os.path.join(cxd, "append.txt")
    open(apf, "w", encoding="utf-8").write("alpha\nbeta\n")
    ao = pp.parse_and_apply("*** Begin Patch\n*** Update File: append.txt\n@@\n+tail\n*** End Patch\n", cxd)
    check("patch_parse: a hunk with no old lines appends at the end of the file", ao[0]["applied"] and ao[0]["after"] == "alpha\nbeta\ntail\n")

    # transcript_io
    js_sample = 'text(await tools.exec_command({cmd:"Get-Content -LiteralPath \'D:\\\\x\\\\a.md\'", max_output_tokens: 20}))'
    check("transcript_io: exec_command, Bash and the code-mode exec JavaScript all yield the command; apply_patch is not a shell call",
          tio.shell_commands("exec_command", {"cmd": "Get-Content a.md"}) == ["Get-Content a.md"]
          and tio.shell_commands("Bash", {"command": "cat a"}) == ["cat a"]
          and tio.shell_commands("exec", {"input": js_sample}) == ["Get-Content -LiteralPath 'D:\\x\\a.md'"]
          and tio.shell_commands("apply_patch", {"command": "x"}) is None)
    ap = "*** Begin Patch\n*** Update File: a.md\n*** End Patch"
    check("transcript_io: apply_patch and spawn_agent map to Claude's Edit and Agent",
          tio.claude_tool_use("apply_patch", {"command": ap})[0] == "Edit" and tio.claude_tool_use("apply_patch", {"command": ap})[1]["file_path"] == "a.md"
          and tio.claude_tool_use("collaboration.spawn_agent", {"task": "x"})[0] == "Agent")
    roll = os.path.join(tmp, "rollout_fixture.jsonl")
    with open(roll, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session_meta", "payload": {"id": "cxs"}}) + "\n")
        f.write(json.dumps({"type": "response_item", "timestamp": "t1", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "do the thing"}]}}) + "\n")
        f.write(json.dumps({"type": "response_item", "timestamp": "t2", "payload": {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": "text(await tools.exec_command({cmd:\"Get-Content 'x.md'\"}))"}}) + "\n")
        f.write(json.dumps({"type": "response_item", "timestamp": "t3", "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": [{"type": "input_text", "text": "Script completed"}]}}) + "\n")
        f.write(json.dumps({"type": "response_item", "timestamp": "t4", "payload": {"type": "reasoning", "summary": []}}) + "\n")
        f.write(json.dumps({"type": "response_item", "timestamp": "t5", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done, wrote it."}]}}) + "\n")
    rows = tio.convert_rollout(roll)
    check("transcript_io: a Codex rollout converts to Claude-shaped user / tool_use(Bash) / tool_result / assistant rows",
          [r["type"] for r in rows] == ["user", "assistant", "user", "assistant"] and rows[0]["message"]["content"] == "do the thing"
          and rows[1]["message"]["content"][0]["name"] == "Bash" and rows[1]["message"]["content"][0]["input"]["command"] == "Get-Content 'x.md'"
          and rows[3]["message"]["content"][0]["text"] == "Done, wrote it.")
    check("transcript_io: an unflushed last_assistant_message is appended as the final assistant row",
          tio.convert_rollout(roll, "Final reply after the flush")[-1]["message"]["content"][0]["text"] == "Final reply after the flush")
    js_patch = 'text(await tools.apply_patch({patch:"*** Begin Patch\\n*** Add File: z.md\\n+hi\\n*** End Patch"}))'
    uses = tio.claude_tool_uses("functions.exec", {"input": js_patch})
    check("transcript_io: apply_patch nested in code-mode JavaScript maps to Edit with the patch's path, not to Bash",
          any(n == "Edit" and i.get("file_path") == "z.md" for n, i in uses) and not any(n == "Bash" for n, i in uses))

    # end to end through the dispatcher copy: a recording stub, a path-blocking stub, a heredoc-blocking stub
    rec = stub(dd, "rec.py", "import sys, json, os, uuid\nd = json.load(sys.stdin)\np = os.path.join(os.environ['REC_FILE'], uuid.uuid4().hex + '.json')\n"
                             "with open(p, 'x', encoding='utf-8') as f:\n    json.dump(d, f)\n")
    blk = stub(dd, "blk.py", "import sys, json\nd = json.load(sys.stdin)\nfp = (d.get('tool_input') or {}).get('file_path', '')\n"
                             "if 'shared_' in fp:\n    sys.stderr.write('BLOCKED shared path\\n'); sys.exit(2)\nsys.exit(0)\n")
    hd = stub(dd, "heredoc_stub.py", "import sys, json\nd = json.load(sys.stdin)\nif '<<' in (d.get('tool_input') or {}).get('command', ''):\n"
                                     "    sys.stderr.write('heredoc blocked\\n'); sys.exit(2)\n")
    reg = {"events": {
        "PreToolUse": [{"matcher": "Edit|Write", "script": "rec.py", "cmd": [sys.executable, rec]},
                       {"matcher": "Edit|Write", "script": "blk.py", "cmd": [sys.executable, blk]},
                       {"matcher": "Bash", "script": "heredoc_stub.py", "cmd": [sys.executable, hd]},
                       {"matcher": "Bash", "script": "rec.py", "cmd": [sys.executable, rec]},
                       {"matcher": "Agent", "script": "rec.py", "cmd": [sys.executable, rec]}],
        "PostToolUse": [{"matcher": "Read", "script": "read_ledger.py", "cmd": [sys.executable, os.path.join(dd, "read_ledger.py")]},
                        {"matcher": "Bash", "script": "rec.py", "cmd": [sys.executable, rec]}],
        "Stop": [{"matcher": "", "script": "rec.py", "cmd": [sys.executable, rec]},
                 {"matcher": "", "script": "claude_only.py", "cmd": [sys.executable, rec], "agents": ["claude"]}],
    }}
    json.dump(reg, open(os.path.join(dd, "hook_registry.json"), "w"))
    rec_dir = os.path.join(tmp, "rec_records")
    os.makedirs(rec_dir)

    def recs():
        return [json.load(open(os.path.join(rec_dir, n), encoding="utf-8")) for n in sorted(os.listdir(rec_dir)) if n.endswith(".json")]

    def reset():
        for n in os.listdir(rec_dir):
            os.remove(os.path.join(rec_dir, n))

    cxt = "C:\\Users\\x\\.codex\\sessions\\2026\\r.jsonl"  # marks a payload as Codex; no file needed before Stop
    rc, o, e = disp(dd, "PreToolUse", {"session_id": "cx2", "transcript_path": cxt, "cwd": cxd, "tool_name": "exec_command",
                                       "tool_input": {"cmd": "cat <<'EOF'\nhi\nEOF"}}, state, REC_FILE=rec_dir)
    try:
        bj = json.loads(o)
    except Exception:
        bj = {}
    check("Codex: exec_command becomes Bash and the heredoc stub blocks it; the block is JSON on stdout (deny + block, exit 0)",
          rc == 0 and "heredoc" in e.lower() and bj.get("decision") == "block" and bj.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
          and any(r["tool_name"] == "Bash" and r["tool_input"]["command"].startswith("cat <<") for r in recs()), f"rc={rc} o={o[:100]!r}")
    rc, o, e = disp(dd, "PreToolUse", {"session_id": "cl3", "transcript_path": "C:\\Users\\x\\.claude\\projects\\p\\s.jsonl",
                                       "tool_name": "Bash", "tool_input": {"command": "cat <<'EOF'\nhi\nEOF"}}, state, REC_FILE=rec_dir)
    check("Claude path unchanged: the same block is exit 2 with the text on stderr and nothing on stdout", rc == 2 and "heredoc" in e.lower() and not o.strip())
    reset()
    patch2 = "*** Begin Patch\n*** Add File: shared_notes\\memo.md\n+hello\n*** Update File: notes.md\n@@\n alpha\n-beta\n+BETA2\n gamma\n*** End Patch\n"
    rc, o, e = disp(dd, "PreToolUse", {"session_id": "cx2", "transcript_path": cxt, "cwd": cxd, "tool_name": "apply_patch", "tool_input": {"command": patch2}}, state, REC_FILE=rec_dir)
    got = recs()
    w_ = [r for r in got if r["tool_name"] == "Write"]
    ed_ = [r for r in got if r["tool_name"] == "Edit"]
    check("Codex: one patch fans out to a Write (Add File) and an Edit (Update File) with old/new strings and the reconstructed file",
          len(w_) == 1 and w_[0]["tool_input"]["file_path"].endswith("memo.md") and w_[0]["tool_input"]["content"] == "hello\n"
          and len(ed_) == 1 and ed_[0]["tool_input"]["old_string"] == "alpha\nbeta\ngamma" and ed_[0]["tool_input"]["_codex_after"] == "alpha\nBETA2\ngamma\ndelta\n")
    check("Codex: a blocked second file blocks the whole patch", rc == 0 and "BLOCKED shared path" in e and '"permissionDecision": "deny"' in o)
    rc, o, e = disp(dd, "PostToolUse", {"session_id": "cx3", "transcript_path": cxt, "cwd": cxd, "tool_name": "exec_command",
                                        "tool_input": {"cmd": "Get-Content -LiteralPath '%s'" % upd}, "tool_response": {"output": "alpha\nbeta"}}, state, REC_FILE=rec_dir)
    led = os.path.join(state, "read_ledger_cx3.jsonl")
    led_rows = [json.loads(line) for line in open(led, encoding="utf-8")] if os.path.isfile(led) else []
    check("Codex read ledger: a whole-file Get-Content is a FULL read with a sha256 and via=codex-shell",
          len(led_rows) == 1 and led_rows[0]["full"] and led_rows[0].get("via") == "codex-shell" and len(led_rows[0].get("sha256", "")) == 64, str(led_rows))
    disp(dd, "PostToolUse", {"session_id": "cx3", "transcript_path": cxt, "cwd": cxd, "tool_name": "exec_command",
                             "tool_input": {"cmd": "Get-Content -LiteralPath '%s' -TotalCount 2" % upd}, "tool_response": {"output": "alpha\nbeta"}}, state, REC_FILE=rec_dir)
    disp(dd, "PostToolUse", {"session_id": "cx3", "transcript_path": cxt, "cwd": cxd, "tool_name": "exec_command",
                             "tool_input": {"cmd": "Get-Content -LiteralPath '%s'" % upd},
                             "tool_response": {"output": "Warning: truncated output (original token count: 20325)\nalpha"}}, state, REC_FILE=rec_dir)
    led_rows = [json.loads(line) for line in open(led, encoding="utf-8")]
    check("Codex read ledger: a -TotalCount read and a truncated-output read are PARTIAL with the reason recorded",
          len(led_rows) == 3 and led_rows[1]["full"] is False and led_rows[1].get("partial_reason") == "ranged read"
          and led_rows[2]["full"] is False and led_rows[2].get("partial_reason") == "truncated output", str(led_rows))
    led9 = os.path.join(state, "read_ledger_cx9.jsonl")

    def led9_rows():
        return [json.loads(line) for line in open(led9, encoding="utf-8")] if os.path.isfile(led9) else []
    disp(dd, "PostToolUse", {"session_id": "cx9", "transcript_path": cxt, "cwd": cxd, "tool_name": "exec_command",
                             "tool_input": {"cmd": "Get-Content -LiteralPath '%s'" % upd}, "tool_response": {"exit_code": 1, "output": "Access denied"}}, state, REC_FILE=rec_dir)
    check("Codex read ledger: a failed command credits nothing", led9_rows() == [])
    disp(dd, "PostToolUse", {"session_id": "cx9", "transcript_path": cxt, "cwd": cxd, "tool_name": "exec_command",
                             "tool_input": {"cmd": "Get-Content -LiteralPath '%s' | Out-Null" % upd}, "tool_response": {"exit_code": 0, "output": ""}}, state, REC_FILE=rec_dir)
    check("Codex read ledger: a read whose output was discarded credits nothing", led9_rows() == [])
    disp(dd, "PostToolUse", {"session_id": "cx9", "transcript_path": cxt, "cwd": cxd, "tool_name": "exec_command",
                             "tool_input": {"cmd": "Get-Content -LiteralPath '%s' | Measure-Object -Line" % upd}, "tool_response": {"exit_code": 0, "output": "Lines: 4"}}, state, REC_FILE=rec_dir)
    check("Codex read ledger: a read piped into a transforming command is PARTIAL with the stage named",
          len(led9_rows()) == 1 and led9_rows()[0]["full"] is False and "piped into Measure-Object" in led9_rows()[0].get("partial_reason", ""))
    reset()
    disp(dd, "Stop", {"session_id": "cx4", "transcript_path": roll, "model": "gpt-6-astra", "last_assistant_message": "Final reply after the flush",
                      "stop_hook_active": False}, state, REC_FILE=rec_dir)
    got = recs()
    conv = got[0]["transcript_path"] if got else ""
    check("Codex Stop: transcript_path is rewritten to a converted Claude-shaped transcript carrying the reply; the Claude-only hook did not run",
          len(got) == 1 and conv != roll and os.path.isfile(conv) and "Final reply after the flush" in open(conv, encoding="utf-8").read()
          and got[0].get("_codex_rollout") == roll, f"n={len(got)}")
    reset()
    disp(dd, "Stop", {"session_id": "cl9", "transcript_path": "C:\\Users\\x\\.claude\\projects\\p\\s.jsonl", "stop_hook_active": False}, state, REC_FILE=rec_dir)
    check("Claude Stop: both hooks run and the transcript path is untouched", len(recs()) == 2 and all(r["transcript_path"].endswith("s.jsonl") for r in recs()))
    reset()
    disp(dd, "PreToolUse", {"session_id": "cx5", "transcript_path": cxt, "tool_name": "collaboration.spawn_agent", "tool_input": {"task": "t"}}, state, REC_FILE=rec_dir)
    check("Codex: collaboration.spawn_agent is dispatched as Agent", any(r["tool_name"] == "Agent" for r in recs()))
    dumpd = os.path.join(tmp, "dumps")
    disp(dd, "PreToolUse", {"session_id": "cx6", "transcript_path": cxt, "tool_name": "exec_command", "tool_input": {"cmd": "echo x"}}, state,
         REC_FILE=rec_dir, CLAUDE_HOOK_DUMP_DIR=dumpd)
    dfs = os.listdir(dumpd) if os.path.isdir(dumpd) else []
    check("CLAUDE_HOOK_DUMP_DIR records the raw and translated payloads", len(dfs) == 1 and "translated" in open(os.path.join(dumpd, dfs[0]), encoding="utf-8").read())
    check.done()
