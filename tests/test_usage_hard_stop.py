"""Usage hard stop: per-session token budget, campaign-state hand-off, and the per-subagent cap."""
import json
import os

from conftest import hook, run_hook

USTOP = hook("usage_hard_stop.py")


def usage_transcript(path, input_tokens_list):
    lines = [json.dumps({"type": "assistant", "uuid": "u%d" % i,
                         "message": {"id": "msg%d" % i, "role": "assistant", "usage": {"input_tokens": n}}})
             for i, n in enumerate(input_tokens_list)]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return path


def test_session_budget_and_campaign_state(tmp_path, state, check):
    tmp = str(tmp_path)
    camp = os.path.join(tmp, "campaign_lib")
    os.makedirs(camp)
    cstate = os.path.join(camp, "CAMPAIGN_STATE.json")
    t5 = os.path.join(tmp, "u.jsonl")

    def set_campaign(mode):
        json.dump({"mode": mode, "mode_note": "n", "in_flight": {}, "keep_me": 1}, open(cstate, "w"))

    def campaign():
        return json.load(open(cstate, encoding="utf-8"))

    def ustop(sid, transcript, cwd, cfg):
        json.dump(cfg, open(os.path.join(state, "usage_budget.json"), "w"))
        return run_hook(USTOP, {"session_id": sid, "transcript_path": transcript, "tool_name": "Read", "cwd": cwd}, state)

    base_cfg = {"armed": True, "weighted_budget_M": 0.001, "warn_frac": 0.7}  # budget = 1,000 tokens, warn at 700
    set_campaign("RUNNING")
    rc, out, _ = ustop("u1", usage_transcript(t5, [800]), camp, base_cfg)
    check("warn fires at 80% of a 1,000-token budget (fixture sanity)", rc == 0 and "ROTATE NOW" in out, out[:120])
    check("campaign_state_path ABSENT -> campaign file untouched", campaign()["mode"] == "RUNNING" and "mode_set_by" not in campaign())
    cfg5 = dict(base_cfg, campaign_state_path=cstate)
    ustop("u2", usage_transcript(t5, [800]), tmp, cfg5)
    check("key present but session cwd outside the campaign folder -> no write", campaign()["mode"] == "RUNNING")
    rc, out, _ = ustop("u3", usage_transcript(t5, [800]), os.path.join(camp, "sub"), cfg5)
    c = campaign()
    check("warning writes mode ROTATE_PENDING into the campaign state (cwd inside), other keys kept",
          c["mode"] == "ROTATE_PENDING" and c["mode_set_by"]["hook"] == "usage_hard_stop.py"
          and c["mode_set_by"]["previous_mode"] == "RUNNING" and c["keep_me"] == 1 and "ROTATE_PENDING" in out, json.dumps(c)[:200])
    rc, out, err = ustop("u3", usage_transcript(t5, [800, 500]), os.path.join(camp, "sub"), cfg5)
    c = campaign()
    check("hard block writes HALTED_BUDGET (from ROTATE_PENDING) and exits 2",
          rc == 2 and c["mode"] == "HALTED_BUDGET" and "HALTED_BUDGET" in err and "over the" in c["mode_set_by"]["reason"], f"rc={rc} {c.get('mode')}")
    set_campaign("PAUSED_GATE")
    ustop("u4", usage_transcript(t5, [800]), camp, cfg5)
    ustop("u4", usage_transcript(t5, [800, 500]), camp, cfg5)
    check("PAUSED_* (human decision) is never overwritten", campaign()["mode"] == "PAUSED_GATE")
    set_campaign("RUNNING")
    ustop("u5", usage_transcript(t5, [1300]), camp, cfg5)
    check("hard block straight from RUNNING also writes HALTED_BUDGET", campaign()["mode"] == "HALTED_BUDGET")
    rc, out, _ = ustop("u6", usage_transcript(t5, [800]), camp, dict(base_cfg, campaign_state_path=os.path.join(tmp, "nowhere", "CAMPAIGN_STATE.json")))
    check("a registered path that does not exist is ignored (fail open, warn still printed)", rc == 0 and "ROTATE NOW" in out)
    rc, out, _ = ustop("u7", usage_transcript(t5, [800]), tmp, base_cfg)
    wj = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    check("the warning reaches the model as PreToolUse additionalContext, not only as systemMessage",
          rc == 0 and "ROTATE NOW" in wj.get("hookSpecificOutput", {}).get("additionalContext", "")
          and wj.get("hookSpecificOutput", {}).get("hookEventName") == "PreToolUse" and "ROTATE NOW" in wj.get("systemMessage", ""), out[:160])

    def ustop_read(sid, fp):
        json.dump(base_cfg, open(os.path.join(state, "usage_budget.json"), "w"))
        return run_hook(USTOP, {"session_id": sid, "transcript_path": usage_transcript(t5, [1300]), "tool_name": "Read",
                                "tool_input": {"file_path": fp}, "cwd": tmp}, state)
    rh = [ustop_read("u8", os.path.join(tmp, n)) for n in ("BATCH04_HANDOFF_2026-09-24.md", "SESSION_STATE_x.md", "Claude_ProjectMemory_T.md")]
    check("over budget, a Read of a HANDOFF / SESSION_STATE / project-memory file passes with an over-budget note",
          all(r[0] == 0 and "Over budget" in r[1] for r in rh), str([r[0] for r in rh]))
    rx = ustop_read("u9", os.path.join(tmp, "events_ledger.jsonl"))
    check("over budget, a Read of any other file still blocks and names the handoff exemption", rx[0] == 2 and "Still allowed" in rx[2])
    rb9 = run_hook(USTOP, {"session_id": "u9", "transcript_path": t5, "tool_name": "Bash", "tool_input": {"command": "cat HANDOFF.md"}, "cwd": tmp}, state)
    check("over budget, Bash stays blocked even when it names a handoff file", rb9[0] == 2)
    check.done()


def test_per_subagent_cap(tmp_path, state, check):
    tmp = str(tmp_path)
    sub_root = os.path.join(tmp, "sub_proj")
    camp = os.path.join(tmp, "campaign_lib")
    os.makedirs(camp)
    cstate = os.path.join(camp, "CAMPAIGN_STATE.json")

    def sub_layout(sid, parent, subs, nested=None):
        """A subagent's PreToolUse payload carries agent_id and the PARENT's transcript_path; the helper's own
        transcript is <dir>/<sid>/subagents/agent-<agent_id>.jsonl (probed on Claude Code 2.1.319)."""
        os.makedirs(sub_root, exist_ok=True)
        main_t = usage_transcript(os.path.join(sub_root, sid + ".jsonl"), parent)
        sdir = os.path.join(sub_root, sid, "subagents")
        for aid, toks in subs.items():
            d = os.path.join(sdir, nested) if nested else sdir
            os.makedirs(d, exist_ok=True)
            usage_transcript(os.path.join(d, "agent-%s.jsonl" % aid), toks)
        return main_t

    sub_cfg = {"armed": True, "weighted_budget_M": 0.01, "warn_frac": 0.7, "subagent_budget_M": 0.001}

    def ustop_sub(sid, main_t, cfg, agent_id=None, tool="Bash", tool_input=None, cwd=None):
        json.dump(cfg, open(os.path.join(state, "usage_budget.json"), "w"))
        pl = {"session_id": sid, "transcript_path": main_t, "tool_name": tool, "tool_input": tool_input or {"command": "echo x"},
              "cwd": cwd or tmp, "hook_event_name": "PreToolUse"}
        if agent_id:
            pl["agent_id"], pl["agent_type"] = agent_id, "general-purpose"
        return run_hook(USTOP, pl, state)

    mt = sub_layout("s1", [100], {"aaa111": [1300], "bbb222": [100]})
    rc, out, err = ustop_sub("s1", mt, sub_cfg, "aaa111")
    check("a helper over its own cap is blocked with the write-to-disk-and-return order",
          rc == 2 and "HELPER USAGE CAP" in err and "Write your results so far to disk" in err and "aaa111" in err and "USAGE HARD STOP" not in err, err[:200])
    rc, out, err = ustop_sub("s1", mt, sub_cfg, "bbb222")
    check("a sibling helper under the cap is not blocked or warned", rc == 0 and not out.strip())
    rc, out, err = ustop_sub("s1", mt, sub_cfg)
    check("the parent session (no agent_id) is not blocked by a helper's cap", rc == 0 and not out.strip())
    mt = sub_layout("s2", [100], {"ccc333": [800]})
    rc, out, _ = ustop_sub("s2", mt, sub_cfg, "ccc333")
    wj = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    check("past warn_frac of the cap the helper gets an additionalContext warning, no systemMessage",
          rc == 0 and "HELPER CAP" in wj.get("hookSpecificOutput", {}).get("additionalContext", "") and "systemMessage" not in wj, out[:200])
    rc, out, _ = ustop_sub("s2", mt, sub_cfg, "ccc333")
    check("the helper warning respects the cooldown", rc == 0 and not out.strip())
    mt = sub_layout("s3", [100], {"aaa111": [1300]})
    check("key absent -> default cap 8M applies (1,300 tokens passes)", ustop_sub("s3", mt, {k: v for k, v in sub_cfg.items() if k != "subagent_budget_M"}, "aaa111")[0] == 0)
    check("armed false disarms the helper cap too", ustop_sub("s3", mt, dict(sub_cfg, armed=False), "aaa111")[0] == 0)
    check("subagent_budget_M 0 disables the helper cap", ustop_sub("s3", mt, dict(sub_cfg, subagent_budget_M=0), "aaa111")[0] == 0)
    mt = sub_layout("s4", [9000], {"ddd444": [1500], "eee555": [100]})
    rc, _, err = ustop_sub("s4", mt, sub_cfg, "eee555")
    check("session over budget still blocks every call, a helper under its cap included", rc == 2 and "USAGE HARD STOP" in err and "HELPER USAGE CAP" not in err)
    mt = sub_layout("s5", [100], {"fff666": [1300]})
    rc, out, _ = ustop_sub("s5", mt, sub_cfg, "fff666", tool="Read", tool_input={"file_path": os.path.join(tmp, "E07_HANDOFF_x.md")})
    rc2, _, _ = ustop_sub("s5", mt, sub_cfg, "fff666", tool="Read", tool_input={"file_path": os.path.join(tmp, "frame_0199.jpg")})
    check("over the cap a helper may Read a handoff file, any other Read blocks", rc == 0 and "Helper over its cap" in out and rc2 == 2)
    mt = sub_layout("s6", [100], {"ggg777": [1300]}, nested=os.path.join("workflows", "w1"))
    check("a helper transcript in a nested subagents folder is found and capped", ustop_sub("s6", mt, sub_cfg, "ggg777")[0] == 2)
    check("a malformed agent_id is ignored (no path walk, fail open)", ustop_sub("s6", mt, sub_cfg, "../../s6")[0] == 0)
    mt = sub_layout("s8", [7000], {"iii999": [800]})
    rc, out, _ = ustop_sub("s8", mt, sub_cfg, "iii999")
    wj = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    ac = wj.get("hookSpecificOutput", {}).get("additionalContext", "")
    check("session and helper warnings together: systemMessage carries the session text only, additionalContext both",
          rc == 0 and "ROTATE NOW" in wj.get("systemMessage", "") and "HELPER CAP" not in wj.get("systemMessage", "") and "ROTATE NOW" in ac and "HELPER CAP" in ac, out[:200])
    mt = sub_layout("s7", [100], {"hhh888": [1300]})
    json.dump({"mode": "RUNNING", "mode_note": "n", "in_flight": {}, "keep_me": 1}, open(cstate, "w"))
    rc, _, _ = ustop_sub("s7", mt, dict(sub_cfg, campaign_state_path=cstate), "hhh888", cwd=camp)
    check("a helper cap block never writes the campaign state",
          rc == 2 and json.load(open(cstate, encoding="utf-8"))["mode"] == "RUNNING")
    check.done()
