"""Turn-end gate: R1 (completion claim with no read-back), R2 (autopilot endings), R3 (claimed send or
launch with no tool call; fixtures are the two replayed incidents, generalized)."""
import json
import os
import subprocess
import sys

from conftest import base_env, hook, run_hook, tent, tool_use as tu

TGATE = hook("turn_end_gate.py")
SMODE = hook("session_mode.py")


def turn_transcript(path, tools, reply, prior_bounce=False, after_tools=(), after_reply=""):
    lines = [tent("user", "do the thing", "2099-01-01T11:00:00.000Z")]
    for i, t in enumerate(tools):
        lines.append(tent("assistant", [t], "2099-01-01T11:00:%02d.000Z" % (i + 1)))
    if reply:
        lines.append(tent("assistant", [{"type": "text", "text": reply}], "2099-01-01T11:01:00.000Z"))
    if prior_bounce:
        lines.append(tent("user", "Stop hook feedback:\n[python hooks/turn_end_gate.py]: [TURN END GATE R1] ...",
                          "2099-01-01T11:01:01.000Z", isMeta=True))
        for i, t in enumerate(after_tools):
            lines.append(tent("assistant", [t], "2099-01-01T11:01:%02d.000Z" % (i + 2)))
        if after_reply:
            lines.append(tent("assistant", [{"type": "text", "text": after_reply}], "2099-01-01T11:02:00.000Z"))
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return path


def test_turn_end_gate(tmp_path, state, check):
    tmp = str(tmp_path)
    tg_cfg = os.path.join(tmp, "tg_on.json")
    json.dump({"R1": True, "R2": True, "hard_mode": False, "shadow_log": True}, open(tg_cfg, "w"))
    tg_off = os.path.join(tmp, "tg_off.json")
    json.dump({"R1": False, "R2": False, "hard_mode": False, "shadow_log": True}, open(tg_off, "w"))
    tg_hard = os.path.join(tmp, "tg_hard.json")
    json.dump({"R1": True, "R2": True, "hard_mode": True, "shadow_log": True}, open(tg_hard, "w"))
    tg_r3 = os.path.join(tmp, "tg_r3.json")
    json.dump({"R1": False, "R2": False, "R3": True, "hard_mode": False, "shadow_log": True}, open(tg_r3, "w"))
    tt = os.path.join(tmp, "t.jsonl")

    def gate(sid, transcript, cfg=tg_cfg, cwd=None, active=False):
        pl = {"session_id": sid, "transcript_path": transcript, "cwd": cwd or tmp, "stop_hook_active": active}
        p = subprocess.run([sys.executable, TGATE], input=json.dumps(pl), capture_output=True, text=True, encoding="utf-8",
                           env=base_env(state, CLAUDE_TURN_END_GATE_CONFIG=cfg), timeout=60)
        return p.returncode, p.stdout, p.stderr

    # R1 by artifact type
    rc1, _, e1 = gate("g1", turn_transcript(tt, [tu("Bash", command="pandoc a.md -o a.pdf")], "✅ PDF ready at a.pdf"))
    rc2, _, e2 = gate("g1", turn_transcript(tt, [tu("Bash", command="pandoc a.md -o a.pdf"), tu("Bash", command="pdftoppm -png -r 60 a.pdf pg"),
                                                 tu("Read", file_path="pg-1.png")], "✅ PDF ready at a.pdf"))
    check("R1 pdf: claim without render bounces; a hand pdftoppm plus one Read without a render manifest still bounces",
          rc1 == 2 and "Name the check that proves it" in e1 and rc2 == 2 and "no render_check manifest" in e2, f"rc={rc1}/{rc2} " + e1[:150])
    rc1, _, _ = gate("g2", turn_transcript(tt, [tu("PowerShell", command="$d.Content.Find.Execute(); $d.Save()")], "Done: the docx is edited."))
    rc2, _, _ = gate("g2", turn_transcript(tt, [tu("PowerShell", command="$d.Save()"),
                                                tu("Bash", command="python hooks/verify_docx.py a.docx --baseline b.json --expect-delta 3")],
                                           "Done: the docx is edited (verify_docx PASS)."))
    check("R1 docx: claim without verify_docx bounces; verify_docx.py counts as a read-back", rc1 == 2 and rc2 == 0, f"rc={rc1}/{rc2}")
    rc1, _, _ = gate("g3", turn_transcript(tt, [tu("PowerShell", command="Start-Process python -ArgumentList run.py")], "Running. The autosave is live."))
    rc2, _, _ = gate("g3", turn_transcript(tt, [tu("PowerShell", command="Start-Process python -ArgumentList run.py"),
                                                tu("PowerShell", command="Get-Process -Id 3201 | Select Id,StartTime")], "Running. PID 3201 queried."))
    check("R1 process: 'Running' without a PID query bounces; Get-Process passes", rc1 == 2 and rc2 == 0, f"rc={rc1}/{rc2}")
    rc1, _, _ = gate("g4", turn_transcript(tt, [tu("Bash", command="start \"\" \"the/folder\"")], "Opened the folder."))
    rc2, _, _ = gate("g4", turn_transcript(tt, [tu("Bash", command="start \"\" \"the/folder\"")], "Folder command issued, confirm you see it."))
    check("R1 folder: 'Opened' bounces; 'command issued, confirm you see it' passes", rc1 == 2 and rc2 == 0, f"rc={rc1}/{rc2}")
    rc1, _, _ = gate("g5", turn_transcript(tt, [tu("mcp__claude_ai_Gmail__send_message", to="x")], "Sent the reply to Sam."))
    rc2, _, _ = gate("g5", turn_transcript(tt, [tu("mcp__claude_ai_Gmail__send_message", to="x"),
                                                tu("mcp__claude_ai_Gmail__get_thread", thread_id="t1")], "Sent the reply to Sam (thread re-read)."))
    check("R1 email: send without get_thread bounces; get_thread passes", rc1 == 2 and rc2 == 0, f"rc={rc1}/{rc2}")
    rc1, _, e1 = gate("g6", turn_transcript(tt, [tu("Read", file_path="x")], "Sent the fix to the transcription session; told it to resolve item 11."))
    rc2, _, _ = gate("g6", turn_transcript(tt, [tu("SendMessage", to="lane2", message="x"), tu("Read", file_path="x")],
                                           "Sent the fix to the transcription session; its reply is in the log."))
    check("R1 peer: message claimed with no SendMessage bounces; SendMessage + read passes", rc1 == 2 and "peer claim" in e1 and rc2 == 0, f"rc={rc1}/{rc2}")
    rc1, _, _ = gate("g7", turn_transcript(tt, [tu("Edit", file_path="a.md")], "**Fixed** the citation in a.md."))
    rc2, _, _ = gate("g7", turn_transcript(tt, [tu("Edit", file_path="a.md"), tu("Read", file_path="a.md")], "Fixed the citation in a.md."))
    rc3, _, _ = gate("g7", turn_transcript(tt, [tu("Edit", file_path="a.md")], "Fixed the citation in a.md ❓ not re-read yet."))
    rc4, _, _ = gate("g7", turn_transcript(tt, [tu("Edit", file_path="a.md")], "✅ deploy.py --verify: HOOK SET IN SYNC"))
    check("R1 file: Edit+claim bounces; re-read passes; open-question marker inside the sentence passes; bare checkmark is not a claim",
          rc1 == 2 and rc2 == 0 and rc3 == 0 and rc4 == 0, f"rc={rc1}/{rc2}/{rc3}/{rc4}")

    # one bounce per turn
    json.dump({"stage": "bounced", "rule": "R1", "why": "claim 'Fixed'", "message": "m"}, open(os.path.join(state, "turn_end_gate_pending_g8.json"), "w"))
    rc1, _, _ = gate("g8", turn_transcript(tt, [tu("Edit", file_path="a.md")], "Fixed the citation.", prior_bounce=True,
                                           after_tools=(tu("Read", file_path="a.md"),), after_reply="Fixed, re-read lines 3-9."))
    logp = os.path.join(state, "turn_end_gate_log.csv")
    logtxt = open(logp, encoding="utf-8").read() if os.path.isfile(logp) else ""
    check("one bounce per turn: prior own feedback entry means exit 0 and the retry outcome is logged",
          rc1 == 0 and "retry" in logtxt and "check added" in logtxt, logtxt[-200:])
    check("stop_hook_active exits 0", gate("g9", turn_transcript(tt, [tu("Edit", file_path="a.md")], "Fixed the citation."), active=True)[0] == 0)

    # R3: the two replayed incident shapes
    rb1 = gate("r3a", turn_transcript(tt, [tu("Bash", command="ls")], "Ten of twelve batch-13 files have transcripts.\n\n"
                                       "Asking the transcription session to resolve the item-11 tail, since they hold the decode history."), cfg=tg_r3)
    rb2 = gate("r3b", turn_transcript(tt, [tu("Bash", command="ls")], "**Batch 5: VERIFIED.**\n\nOne lane running. Dispatching three verification windows."), cfg=tg_r3)
    check("R3: 'Asking the transcription session' with no SendMessage and 'Dispatching three verification windows' with no Agent both bounce",
          rb1[0] == 2 and "[TURN END GATE R3]" in rb1[2] and "SendMessage" in rb1[2] and rb2[0] == 2 and "launched nothing" in rb2[2], f"rc={rb1[0]}/{rb2[0]}")
    rp1 = gate("r3c", turn_transcript(tt, [tu("SendMessage", to="lane2", message="x")], "Asking the transcription session to resolve the item-11 tail."), cfg=tg_r3)
    rp2 = gate("r3d", turn_transcript(tt, [tu("Agent", prompt="x"), tu("Agent", prompt="y")], "Dispatching three verification windows."), cfg=tg_r3)
    rp3 = gate("r3e", turn_transcript(tt, [tu("Bash", command="ls")], "Next I'll message the batch-12 session, then dispatch two lanes. Should I ask the peer first?"), cfg=tg_r3)
    rp4 = gate("r3f", turn_transcript(tt, [tu("Bash", command="nohup python run.py > log 2>&1 &")], "Launching 3 lanes for items 8 to 10."), cfg=tg_r3)
    check("R3: the matching tool (SendMessage, Agent, a shell launch) passes; future and question phrasing passes",
          rp1[0] == 0 and rp2[0] == 0 and rp3[0] == 0 and rp4[0] == 0, f"rc={rp1[0]}/{rp2[0]}/{rp3[0]}/{rp4[0]}")
    lines = [tent("user", "start the lanes", "2099-01-01T10:00:00.000Z"),
             tent("assistant", [tu("Agent", prompt="fix item 9")], "2099-01-01T10:00:05.000Z"),
             tent("user", "status?", "2099-01-01T11:00:00.000Z"),
             tent("assistant", [tu("Bash", command="ls")], "2099-01-01T11:00:05.000Z"),
             tent("assistant", [{"type": "text", "text": "I told the item-9 fix lane to quote the source row. It is still running."}], "2099-01-01T11:01:00.000Z")]
    open(tt, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    rr1 = gate("r3g", tt, cfg=tg_r3)
    rr2 = gate("r3h", turn_transcript(tt, [tu("Bash", command="ls")], "I told the item-9 fix lane to quote the source row."), cfg=tg_r3)
    check("R3: 'I told the lane' passes when an Agent call sits in a recent turn, bounces when none does",
          rr1[0] == 0 and rr2[0] == 2 and "last 4 turns" in rr2[2], f"rc={rr1[0]}/{rr2[0]}")
    rq = gate("r3j", turn_transcript(tt, [tu("Bash", command="ls")],
                                     "Past-tense lines like \"I told the lane\" pass if the call is recent; \"Dispatching three windows\" is the incident."), cfg=tg_r3)
    check("R3: phrases inside double quotes are examples, not claims (first live false bounce)", rq[0] == 0)
    check("R3: with the R3 flag off it only shadow-logs", gate("r3i", turn_transcript(tt, [tu("Bash", command="ls")], "Dispatching three verification windows."), cfg=tg_off)[0] == 0)

    # R2 in autopilot
    run_hook(SMODE, {"session_id": "g10", "prompt": "MODE: autopilot"}, state)
    rc1, _, e1 = gate("g10", turn_transcript(tt, [tu("Read", file_path="x")], "Two options, A or B. Which do you prefer?"))
    rc2, _, _ = gate("g10", turn_transcript(tt, [tu("Read", file_path="x")], "Progress: item 9 second read.\n[14:20] done: item 8 | running: item 9 | blocked: none | next: item 10 | needs you: none"))
    rc3, _, _ = gate("g10", turn_transcript(tt, [tu("Edit", file_path="x")], "I'd apply the 53 edits, your call."))
    check("R2 autopilot: final '?' bounces with the autopilot message; status line passes; 'your call' bounces",
          rc1 == 2 and "Autopilot. Take the option you recommended" in e1 and rc2 == 0 and rc3 == 2, f"rc={rc1}/{rc2}/{rc3}")
    check("R2: inert outside autopilot", gate("g11", turn_transcript(tt, [tu("Read", file_path="x")], "Two options, A or B. Which do you prefer?"))[0] == 0)
    camp = os.path.join(tmp, "campaign")
    os.makedirs(camp)
    json.dump({"mode": "RUNNING", "in_flight": {}, "mode_note": "Next unit: finish item 41 second read."}, open(os.path.join(camp, "CAMPAIGN_STATE.json"), "w"))
    rc1, _, e1 = gate("g10", turn_transcript(tt, [tu("Read", file_path="x")], "Reviewed the ledger; all consistent."), cwd=camp)
    rc2, _, _ = gate("g10", turn_transcript(tt, [tu("Read", file_path="x")], "Reviewed the ledger. [BLOCKED: GPU busy until 15:00]"), cwd=camp)
    rc3, _, _ = gate("g10", turn_transcript(tt, [tu("Read", file_path="x")], "Reviewed the ledger; all consistent."), cwd=tmp)
    check("R2 zero-ACTION: fires only with an unclaimed campaign item, names it, and [BLOCKED] passes",
          rc1 == 2 and "finish item 41 second read" in e1 and rc2 == 0 and rc3 == 0, f"rc={rc1}/{rc2}/{rc3}")
    os.makedirs(os.path.join(state, "usage_meter"), exist_ok=True)
    json.dump({"last_warn": 1.0}, open(os.path.join(state, "usage_meter", "g10.json"), "w"))
    check("R2: silent after a ROTATE order (usage_meter last_warn)",
          gate("g10", turn_transcript(tt, [tu("Read", file_path="x")], "Handoff written. Which lane first?"))[0] == 0)

    # flags off: shadow row, no bounce
    rc1, _, _ = gate("g12", turn_transcript(tt, [tu("Edit", file_path="a.md")], "Fixed the citation."), cfg=tg_off)
    logtxt = open(logp, encoding="utf-8").read()
    check("flags false -> exit 0 and a shadow row is logged (the shipping state for R1/R2)", rc1 == 0 and ",g12,R1,shadow," in logtxt)

    # hard mode
    json.dump({"stage": "bounced", "rule": "R1", "why": "claim 'Fixed'", "message": "Name the check."},
              open(os.path.join(state, "turn_end_gate_pending_g13.json"), "w"))
    rc1, _, _ = gate("g13", turn_transcript(tt, [tu("Edit", file_path="a.md")], "Fixed the citation.", prior_bounce=True,
                                            after_reply="It is fixed, moving on."), cfg=tg_hard)
    rc2, _, e2 = gate("g13", turn_transcript(tt, [tu("Read", file_path="b.md")], "Next item read."), cfg=tg_hard)
    rc3, _, _ = gate("g13", turn_transcript(tt, [tu("Read", file_path="b.md")], "Next item read."), cfg=tg_hard)
    check("hard mode: a retry carrying neither check nor label bounces once more on the next turn",
          rc1 == 0 and rc2 == 2 and "hard mode" in e2 and rc3 == 0, f"rc={rc1}/{rc2}/{rc3}")
    check.done()
