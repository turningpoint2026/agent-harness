"""Session mode from the user's own signals, the correction latch, and the discuss-mode gate."""
import json
import os
import subprocess
import sys

from conftest import base_env, hook, load_module, run_hook, tent

SMODE = hook("session_mode.py")
DGATE = hook("discuss_mode_gate.py")


def test_session_mode_and_discuss_gate(tmp_path, state, check):
    tmp = str(tmp_path)

    def mode_of(sid):
        p = os.path.join(state, "session_mode_%s.json" % sid)
        return json.load(open(p, encoding="utf-8")).get("mode") if os.path.isfile(p) else "MISSING"

    def prompt(sid, text, transcript=None):
        pl = {"session_id": sid, "prompt": text}
        if transcript:
            pl["transcript_path"] = transcript
        return run_hook(SMODE, pl, state)

    # signals in precedence order
    rc, out, _ = prompt("m1", "MODE: autopilot\nStage 1 handoff. Read in this order...")
    check("mode: MODE: line sets autopilot and injects the change line", rc == 0 and mode_of("m1") == "autopilot" and "[MODE CHANGE]" in out, out[:120])
    rc, out, _ = prompt("m1", "stop suggesting end of the day")
    check("mode: bare 'stop' does not clear autopilot", mode_of("m1") == "autopilot" and out.strip() == "")
    rc, out, _ = prompt("m1", "pause")
    check("mode: 'pause' clears autopilot", mode_of("m1") == "default" and "-> default" in out, out[:120])
    prompt("m2", "ok keep going with the next one")
    check("mode: phrase 'keep going' sets autopilot", mode_of("m2") == "autopilot")
    prompt("m3", "lets discuss the findings first")
    check("mode: 'lets discuss' sets discuss", mode_of("m3") == "discuss")
    prompt("m3", "dont go ahead yet, one more question")
    check("mode: negated go-phrase does not lift discuss", mode_of("m3") == "discuss")
    prompt("m3", "go")
    check("mode: bare 'go' does not lift discuss", mode_of("m3") == "discuss")
    prompt("m3", "good, go ahead")
    check("mode: 'go ahead' lifts discuss", mode_of("m3") == "default")
    prompt("m4", "<task-notification>agent done. keep going with item 9</task-notification>")
    prompt("m4", "Stop hook feedback:\n[python x.py]: MODE: autopilot")
    check("mode: machine-origin text never stamps", mode_of("m4") in ("MISSING", "default"))
    prompt("m5", "no you just print the instruction, i paste to new session")
    check("mode: 'just print the instruction' does not set discuss", mode_of("m5") in ("MISSING", "default"))
    prompt("m6", "/plan")
    check("mode: typed /plan sets discuss", mode_of("m6") == "discuss")
    prompt("m7", "<command-name>/autopilot</command-name>\n<command-message>autopilot</command-message>")
    check("mode: /autopilot command echo sets autopilot", mode_of("m7") == "autopilot")

    # plan-mode events in the transcript
    tdir = os.path.join(tmp, "transcripts")
    os.makedirs(tdir)
    t_plan = os.path.join(tdir, "plan.jsonl")
    open(t_plan, "w", encoding="utf-8").write("\n".join([
        tent("user", "how should we restructure section 3", "2099-01-01T10:00:00.000Z"),
        tent("assistant", [{"type": "tool_use", "id": "tu1", "name": "EnterPlanMode", "input": {}}], "2099-01-01T10:00:05.000Z"),
        tent("user", [{"type": "tool_result", "tool_use_id": "tu1", "content": "Entered plan mode."}], "2099-01-01T10:00:06.000Z"),
    ]) + "\n")
    rc, out, _ = prompt("m8", "what about the tables", t_plan)
    check("mode: EnterPlanMode in the transcript sets discuss", mode_of("m8") == "discuss" and "EnterPlanMode" in out, out[:120])
    with open(t_plan, "a", encoding="utf-8") as fh:
        fh.write("\n".join([
            tent("assistant", [{"type": "tool_use", "id": "tu2", "name": "ExitPlanMode", "input": {}}], "2099-01-01T10:05:00.000Z"),
            tent("user", [{"type": "tool_result", "tool_use_id": "tu2", "content": "The user doesn't want to proceed with this tool use."}], "2099-01-01T10:05:01.000Z"),
        ]) + "\n")
    os.environ["CLAUDE_HOOK_STATE_DIR"] = state
    try:
        smod = load_module("session_mode_t", SMODE)
    finally:
        os.environ.pop("CLAUDE_HOOK_STATE_DIR", None)
    smod.STATE_DIR = state
    check("mode: rejected ExitPlanMode keeps discuss", smod.effective_mode("m8", t_plan)[0] == "discuss")
    with open(t_plan, "a", encoding="utf-8") as fh:
        fh.write("\n".join([
            tent("assistant", [{"type": "tool_use", "id": "tu3", "name": "ExitPlanMode", "input": {}}], "2099-01-01T10:06:00.000Z"),
            tent("user", [{"type": "tool_result", "tool_use_id": "tu3", "content": "User has approved your plan. You can now start coding."}], "2099-01-01T10:06:01.000Z"),
        ]) + "\n")
    check("mode: approved ExitPlanMode lifts discuss (consumer view, no write)", smod.effective_mode("m8", t_plan)[0] == "default")
    check("mode: missing state file reads as default", smod.effective_mode("never-seen")[0] == "default")
    p = subprocess.run([sys.executable, SMODE, "--set", "autopilot", "--signal", "/autopilot", "--session", "m9"],
                       capture_output=True, text=True, encoding="utf-8", env=base_env(state))
    check("mode: CLI --set writes the state file (the /autopilot skill path)",
          p.returncode == 0 and mode_of("m9") == "autopilot" and "[MODE CHANGE]" in p.stdout, p.stdout[:120] + p.stderr[:120])

    # correction latch
    prompt("m2", "are you sure? check again, the folder is empty")
    prompt("m2", "<task-notification>are you sure</task-notification>")
    prompt("m2", "MODE: autopilot\n" + "are you sure " * 40)
    rows = json.load(open(os.path.join(state, "session_mode_m2.json"), encoding="utf-8")).get("corrections", [])
    check("latch: family prompts recorded verbatim; machine text and handoff pastes are not",
          len(rows) == 2 and rows[0]["family"] == "F2_push_forward" and rows[1]["family"] == "F1_check"
          and rows[1]["quote"].startswith("are you sure? check again"), str(rows)[:200])

    # discuss_mode_gate
    prompt("d1", "lets discuss this before you build")

    def dg(sid, tool, fp=None):
        pl = {"session_id": sid, "tool_name": tool, "tool_input": {"file_path": fp} if fp else {"prompt": "x"}}
        return run_hook(DGATE, pl, state)
    rc1, _, e1 = dg("d1", "Write", os.path.join(tmp, "000PROJECT_G", "section_3.md"))
    rc2, _, _ = dg("d1", "Write", os.path.join(tmp, "000PROJECT_G", "section_3.md"))
    check("discuss gate: project write blocked on every attempt; message names the signal and the lifting phrases",
          rc1 == 2 and rc2 == 2 and "lets discuss" in e1 and "go ahead" in e1 and "apply" in e1, e1[:200])
    ex = [os.path.join(tmp, "x", "scratchpad", "notes.md"), os.path.join(tmp, "_loop_work", "run", "PLAN.md"),
          os.path.join(tmp, "000PROJECT_G", "SESSION_STATE_2026.md"), os.path.join(tmp, "000PROJECT_G", "Claude_ProjectMemory_G.md"),
          os.path.join(tmp, "000PROJECT_G", "_BATCH12_HANDOFF.md"), os.path.join(tmp, "registers", "REGISTER_harness_changes.md"),
          os.path.join(tmp, "x", "LESSONS_CAMPAIGN.md")]
    check("discuss gate: scratchpad, _loop_work, SESSION_STATE, project memory, handoff, register appends exempt",
          all(dg("d1", "Write", p)[0] == 0 for p in ex))
    rc1, _, e1 = dg("d1", "Agent")
    check("discuss gate: worker spawn blocked in discuss mode", rc1 == 2 and "Agent spawn" in e1, e1[:120])
    rc1, _, _ = dg("d-none", "Write", os.path.join(tmp, "000PROJECT_G", "section_3.md"))
    rc2, _, _ = dg("m9", "Agent")
    check("discuss gate: inert in default and autopilot modes", rc1 == 0 and rc2 == 0)
    check.done()
