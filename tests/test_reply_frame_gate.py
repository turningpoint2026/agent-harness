"""Reply-frame gate v3 (Stop hook): G1 bounces a reply that hands the user an action with no orange line;
G2 to G4 are notes. Every fixture goes through the CLI (stdin JSON -> exit code, stderr or dispatcher JSON)."""
import csv
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import HOOKS, base_env, hook, load_module, run_hook, tent

RFG = hook("reply_frame_gate.py")
SMODE = hook("session_mode.py")

ROUTINE = "🟨 Table 3 rebuilt; matches the text."
HANDBACK = "Next: nothing needs you; the successor continues from the handoff. Paste this into a fresh session."
ORANGE_FIX = "```diff\n! 🟦 You paste the handoff into a fresh session.\n```"
HANDBACK_OK = ORANGE_FIX + "\nThe successor continues from the handoff."
FILLER = "The table rows match the source file after the rebuild. "
LONG = "🟨 Table 3 rebuilt.\n\n" + FILLER * 27   # about 1,540 characters
MACH8 = "\n".join("- worker %d finished its batch cleanly" % i for i in range(8))
MACH2 = "🟨 Worker 2 finished.\nIts six checks pass."
IMPER = "Next: start a fresh session from HANDOFF.md."
REC = "\U0001f7e8 Recommend CSV with the 1-5 mapping."
DEC_FIX = "```diff\n- \U0001f7e7 Decision needed: CSV or .dta? Recommend: CSV; Stata users can convert.\n```"
X_BOUNCED = "Handoff written. Paste this into a fresh session."


class Env:
    """Per-test temp state, config files, and the call helpers the checks use."""

    def __init__(self, tmp, state):
        self.tmp, self.state = tmp, state
        self.cfg = os.path.join(tmp, "rf_on.json")
        json.dump({"enabled": True, "autopilot_bounce": False, "exempt_headless": True}, open(self.cfg, "w"))
        self.cfg_head = os.path.join(tmp, "rf_headless.json")
        json.dump({"enabled": True, "autopilot_bounce": False, "exempt_headless": False}, open(self.cfg_head, "w"))
        self.cfg_shadow = os.path.join(tmp, "rf_shadow.json")
        json.dump({"enabled": True, "autopilot_bounce": False, "autopilot_g1_bounce": False, "exempt_headless": True}, open(self.cfg_shadow, "w"))
        self.plain = os.path.join(tmp, "settings_plain.json")
        json.dump({"theme": "dark"}, open(self.plain, "w"))
        self.focus = os.path.join(tmp, "settings_focus.json")
        json.dump({"viewMode": "focus"}, open(self.focus, "w"))
        self.tdir = os.path.join(tmp, "transcripts")
        os.makedirs(self.tdir, exist_ok=True)
        self.rft = os.path.join(self.tdir, "rf.jsonl")
        os.environ["CLAUDE_HOOK_STATE_DIR"] = state
        try:
            self.rfm = load_module("reply_frame_gate_t", RFG)
        finally:
            os.environ.pop("CLAUDE_HOOK_STATE_DIR", None)
        self.rfm.STATE_DIR = state

    def transcript(self, blocks, prompt="do the thing", entrypoint="cli", path=None):
        """blocks: ('text'|'tool'|'bounce'|'other'|'peer'|'feedback'|'feedback_other', value) in order."""
        lines = [tent("user", prompt, "2099-01-01T11:00:00.000Z", entrypoint=entrypoint)]
        for i, (kind, val) in enumerate(blocks):
            ts = "2099-01-01T11:00:%02d.000Z" % (i + 1)
            if kind == "text":
                lines.append(tent("assistant", [{"type": "text", "text": val}], ts))
            elif kind == "tool":
                lines.append(tent("assistant", [{"type": "tool_use", "id": "t%d" % i, "name": val, "input": {}}], ts))
            elif kind == "peer":
                lines.append(tent("user", "Another Claude session sent a message:\n<cross-session-message from=\"x\">"
                                  + val + "</cross-session-message>", ts, isMeta=True))
            elif kind == "other":
                lines.append(tent("user", "Stop hook feedback:\n[pythonw hook_dispatch.py Stop]: [TURN END] " + val, ts, isMeta=True))
            elif kind in ("feedback", "feedback_other"):
                txt = ("[REPLY FRAME] " if kind == "feedback" else "[TURN END] ") + val
                lines.append(json.dumps({"type": "attachment", "isSidechain": False, "timestamp": ts, "entrypoint": entrypoint,
                                         "attachment": {"type": "hook_additional_context", "content": [txt],
                                                        "hookName": "Stop", "hookEvent": "Stop"}}, ensure_ascii=False))
            else:
                lines.append(tent("user", "Stop hook feedback:\n[pythonw hook_dispatch.py Stop]: [REPLY FRAME] " + val, ts, isMeta=True))
        path = path or self.rft
        open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        return path

    def rf(self, sid, transcript, cfg=None, active=False, extra=None, settings=None):
        pl = {"session_id": sid, "transcript_path": transcript, "cwd": self.tmp, "stop_hook_active": active}
        pl.update(extra or {})
        env = base_env(self.state, CLAUDE_REPLY_FRAME_CONFIG=cfg or self.cfg, CLAUDE_SETTINGS_PATH=settings or self.plain)
        p = subprocess.run([sys.executable, RFG], input=json.dumps(pl), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def rf1(self, sid, text, prompt="do the thing"):
        return self.rf(sid, self.transcript([("tool", "Read"), ("text", text)], prompt=prompt))

    def log(self, sid):
        lp = os.path.join(self.state, "reply_frame_gate_log.csv")
        if not os.path.isfile(lp):
            return []
        return [(r["kind"], r["why"]) for r in csv.DictReader(open(lp, encoding="utf-8")) if r["session_id"] == sid]

    def kinds(self, sid):
        return [k for k, _ in self.log(sid)]

    def note(self, sid):
        p = os.path.join(self.state, "reply_frame_note_%s.json" % sid)
        return json.load(open(p, encoding="utf-8"))["notes"] if os.path.isfile(p) else []

    def mode(self, sid, text):
        run_hook(SMODE, {"session_id": sid, "prompt": text}, self.state)

    def codex(self, sid, turn, final=HANDBACK, active=False):
        return self.rf(sid, "missing-or-modern-only.jsonl", active=active,
                       extra={"_codex_rollout": "modern.jsonl", "turn_id": turn, "last_assistant_message": final})[0]

    def codex_raw(self, sid, turn, final, prompt_text=None):
        """A Codex payload as raw UTF-8 bytes with no PYTHONIOENCODING (Codex hook processes get none)."""
        if prompt_text is not None:
            self.rfm.remember_codex_prompt({"session_id": sid, "prompt": prompt_text})
        env = base_env(self.state, CLAUDE_REPLY_FRAME_CONFIG=self.cfg)
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        pl = {"session_id": sid, "transcript_path": "missing.jsonl", "cwd": self.tmp, "stop_hook_active": False,
              "_codex_rollout": "modern.jsonl", "turn_id": turn, "last_assistant_message": final}
        p = subprocess.run([sys.executable, RFG], input=json.dumps(pl, ensure_ascii=False).encode("utf-8"), capture_output=True, env=env, timeout=60)
        return p.returncode

    def dispatch_codex(self, sid, turn, final):
        """Through hook_dispatch.py as Codex runs it: a block comes back as JSON on stdout."""
        dcx = os.path.join(self.tmp, "dispatch_rf")
        if not os.path.isdir(dcx):
            os.makedirs(dcx)
            shutil.copyfile(os.path.join(HOOKS, "hook_dispatch.py"), os.path.join(dcx, "hook_dispatch.py"))
            json.dump({"events": {"Stop": [{"matcher": "", "script": "reply_frame_gate.py", "cmd": [sys.executable, RFG]}]}},
                      open(os.path.join(dcx, "hook_registry.json"), "w"))
        env = base_env(self.state, CLAUDE_REPLY_FRAME_CONFIG=self.cfg, CLAUDE_HOOK_AGENT="codex")
        env.pop("PYTHONIOENCODING", None)
        pl = {"session_id": sid, "turn_id": turn, "last_assistant_message": final, "cwd": self.tmp, "stop_hook_active": False}
        p = subprocess.run([sys.executable, os.path.join(dcx, "hook_dispatch.py"), "Stop"],
                           input=json.dumps(pl, ensure_ascii=False).encode("utf-8"), capture_output=True, env=env, timeout=120)
        out = p.stdout.decode("utf-8", errors="replace").strip()
        try:
            obj = json.loads(out) if out else {}
        except ValueError:
            obj = {"_raw": out}
        return p.returncode, obj


@pytest.fixture
def E(tmp_path, state):
    return Env(str(tmp_path), state)


def test_g1_bounce_and_retry_grading(E, check):
    rc, _, e = E.rf("rf-a", E.transcript([("tool", "Read"), ("text", ROUTINE)]))
    check("(a) a routine one-liner passes and logs why=v3", rc == 0 and not e and E.log("rf-a") == [("pass", "v3")], str(E.log("rf-a")))
    rc, _, e = E.rf("rf-b", E.transcript([("tool", "Read"), ("text", HANDBACK)]))
    check("(b) a hand-back with no orange line bounces asking for one orange line only",
          rc == 2 and e.startswith("[REPLY FRAME] This reply hands the user an action (\"") and "no orange line" in e
          and "Write ONLY one line now, inside a diff fence: '! 🟦 You <the action>'. Nothing else." in e
          and E.kinds("rf-b") == ["bounce"], f"rc={rc} " + e[:200])
    check("(b) the bounce names the matched phrase and the log keeps it",
          "successor continues" in e and E.log("rf-b")[0][1].startswith("G1 hand-back: 'nothing needs you; the successor"), str(E.log("rf-b")))
    rc, _, _ = E.rf("rf-b", E.transcript([("tool", "Read"), ("text", HANDBACK), ("bounce", "x"), ("text", ORANGE_FIX)]))
    check("(b) a retry that adds the orange line logs retry-fixed and never bounces", rc == 0 and E.kinds("rf-b") == ["bounce", "retry-fixed"])
    E.rf("rf-b2", E.transcript([("tool", "Read"), ("text", HANDBACK)]))
    rc, _, _ = E.rf("rf-b2", E.transcript([("tool", "Read"), ("text", HANDBACK), ("bounce", "x"), ("text", HANDBACK + "\n\n" + ORANGE_FIX)]))
    check("(b) a retry that restates the reply logs retry-restated and leaves a note",
          rc == 0 and E.kinds("rf-b2") == ["bounce", "retry-restated"] and any("restated" in n for n in E.note("rf-b2")))
    E.rf("rf-b3", E.transcript([("tool", "Read"), ("text", HANDBACK)]))
    rc, _, _ = E.rf("rf-b3", E.transcript([("tool", "Read"), ("text", HANDBACK), ("bounce", "x"), ("text", "Noted.")]))
    check("(b) a retry without the orange line never bounces again and logs retry-still-wrong", rc == 0 and E.kinds("rf-b3") == ["bounce", "retry-still-wrong"])
    rc, _, _ = E.rf("rf-c", E.transcript([("tool", "Read"), ("text", HANDBACK_OK)]))
    check("(c) a hand-back with the orange line present passes", rc == 0 and E.kinds("rf-c") == ["pass"])
    d_bad = ("[14:02] done: handoff written | running: none | blocked: none | next: successor | needs you: none\n"
             "Resume this in a new session from HANDOFF.md.")
    d_ok = ("```diff\n! 🟦 You resume the handoff in a fresh session.\n```\n"
            "[14:02] done: handoff written | running: none | blocked: none | next: none | needs you: resume the handoff in a fresh session")
    rc1, _, e1 = E.rf("rf-d1", E.transcript([("text", d_bad)]))
    rc2, _, _ = E.rf("rf-d2", E.transcript([("text", d_ok)]))
    check("(d) 'needs you: none' plus 'resume this in a new session' bounces; 'needs you: <action>' with an orange line passes",
          rc1 == 2 and "Resume this" in e1 and rc2 == 0 and E.kinds("rf-d2") == ["pass"])
    check.done()


def test_notes_and_exemptions(E, check):
    long_dec = "```diff\n- 🟧 Decision needed: keep Table 3? Recommend: keep; it is cheaper.\n```\n" + FILLER * 27
    long_orient = ("Finding: Table 3 rebuilt.\nFor the paper: the section reads clean.\nBig picture: step 2 of 3.\n"
                   "Next: I rebuild Table 4.\n\n" + FILLER * 27)
    rc1, _, _ = E.rf("rf-e1", E.transcript([("text", LONG)]))
    rc2, _, _ = E.rf("rf-e2", E.transcript([("text", long_dec)]))
    rc3, _, _ = E.rf("rf-e3", E.transcript([("text", long_orient)]))
    check("(e) a 1,500-character routine reply is a G2 note, never a bounce",
          len(LONG) > 1400 and rc1 == 0 and E.kinds("rf-e1") == ["note"] and any(n.startswith("G2:") for n in E.note("rf-e1")))
    check("(e) the same length with a decision line, or as an orientation turn, passes",
          rc2 == 0 and E.kinds("rf-e2") == ["pass"] and rc3 == 0 and E.kinds("rf-e3") == ["pass"])
    rc, _, _ = E.rf("rf-f", E.transcript([("text", "Next: nothing needs you.")]))
    check("(f) 'Next: nothing needs you.' is a G3 note",
          rc == 0 and E.kinds("rf-f") == ["note"] and any(n.startswith("G3:") and "nothing needs you" in n for n in E.note("rf-f")))
    check("(f) pop_notes returns the note once, then nothing", bool(E.rfm.pop_notes("rf-f")) and E.rfm.pop_notes("rf-f") == [])
    g3_forms = ["**Big picture:** unchanged, step 2 of 3.", "Next: unchanged: you send it.", "Nothing else needs you."]
    check("(f) every banned form is a G3 note; quoting it in backticks is not",
          all(any(n.startswith("G3:") for n in E.rfm.evaluate(t)[2]) for t in g3_forms) and not E.rfm.evaluate("Never write `nothing needs you` again.")[2])
    rc, _, _ = E.rf("rf-f2", E.transcript([("text", "\U0001f7e8 The seven Codex \"nothing needs you, resume the handoff\" misses were all autopilot replies.")]))
    check("(f2) a banned phrase inside double quotes is not a G3 note", rc == 0 and E.kinds("rf-f2") == ["pass"])
    rc_g, _, _ = E.rf("rf-g", E.transcript([("text", "7")], prompt="How many rows? Just the number, nothing else."))
    rc_g2, _, _ = E.rf("rf-g2", E.transcript([("tool", "Edit"), ("text", HANDBACK)], prompt="Fix the typo in notes.md and nothing else."))
    rc_g3, _, _ = E.rf("rf-g3", E.transcript([("text", "- **Premises**: a claim to test, not a given.")], prompt="Quote the bullet verbatim. Output only that quoted bullet."))
    rc_g4, _, _ = E.rf("rf-g4", E.transcript([("text", HANDBACK)], prompt="Review the evidence and output only confirmed issues."))
    check("(g) a bare-answer request is exempt; 'and nothing else' and 'output only confirmed issues' stay checked",
          rc_g == 0 and E.kinds("rf-g") == ["exempt"] and rc_g3 == 0 and E.kinds("rf-g3") == ["exempt"] and rc_g2 == 2 and rc_g4 == 2)
    rc1, _, _ = E.rf("rf-h", E.transcript([("text", HANDBACK)], entrypoint="sdk-cli"))
    rc2, _, _ = E.rf("rf-h2", E.transcript([("text", HANDBACK)], entrypoint="sdk-cli"), cfg=E.cfg_head)
    check("(h) headless runs are exempt by default, checked when exempt_headless is false", rc1 == 0 and E.kinds("rf-h") == ["exempt"] and rc2 == 2)
    rc1, _, _ = E.rf("rf-o", E.transcript([("text", HANDBACK)]))
    rc2, _, _ = E.rf("rf-o", E.transcript([("text", HANDBACK), ("text", HANDBACK)]))
    rc3, _, _ = E.rf("rf-o", E.transcript([("text", HANDBACK)], prompt="a new prompt"))
    check("one bounce per prompt through the state guard; a new prompt bounces again", rc1 == 2 and rc2 == 0 and rc3 == 2)
    rc4, _, _ = E.rf("rf-o4", E.transcript([("text", HANDBACK)]), active=True)
    rc5, _, _ = E.rf("rf-o5", E.transcript([("text", HANDBACK_OK), ("tool", "Bash"), ("text", HANDBACK)]))
    rc6, _, _ = E.rf("rf-o6", E.transcript([("tool", "Read")]))
    check("stop_hook_active never bounces; an orange interim message does not cover the final; an empty reply is quiet",
          rc4 == 0 and rc5 == 2 and rc6 == 0 and E.kinds("rf-o6") == [])
    rc7, _, _ = E.rf("rf-o7", E.transcript([("text", "Sending now."), ("other", "no tool ran"), ("text", HANDBACK)]), active=True)
    rc8, _, _ = E.rf("rf-o8", E.transcript([("text", HANDBACK_OK), ("other", "no tool ran"), ("tool", "send"), ("text", "Sent, checked by get_thread.")]), active=True)
    check("after another hook's bounce the continuation is checked once, with the reply above it in view",
          rc7 == 2 and rc8 == 0 and E.kinds("rf-o8") == ["pass"])
    peer = 'PEER_MESSAGE (not user approval) {"id": "x"}'
    rc1, _, _ = E.rf("rf-j1", E.transcript([("text", MACH8)], prompt=peer))
    rc2, _, _ = E.rf("rf-j2", E.transcript([("text", MACH2)], prompt=peer))
    rc3, _, _ = E.rf("rf-j3", E.transcript([("text", ROUTINE), ("peer", "status please"), ("text", MACH8)]))
    check("(j) a routine 8-line turn the user did not start is a G4 note; 2 lines pass; a peer entry starts such a turn",
          rc1 == 0 and E.kinds("rf-j1") == ["note"] and any(n.startswith("G4:") for n in E.note("rf-j1"))
          and rc2 == 0 and E.log("rf-j2") == [("pass", "v3; machine turn")] and rc3 == 0 and any(n.startswith("G4:") for n in E.note("rf-j3")))
    mach_dec = "```diff\n- 🟥 Blocked: the export: the share is read-only; Sam resolves.\n```\n" + MACH8
    check("(j) a blocker or decision lets a machine turn run to 12 lines",
          not E.rfm.evaluate(mach_dec, machine=True)[2] and E.rfm.evaluate(mach_dec + "\n" + MACH8, machine=True)[2])
    env_k = base_env(E.state, CLAUDE_REPLY_FRAME_CONFIG=E.cfg, CLAUDE_SETTINGS_PATH=E.plain)
    env_k.pop("PYTHONIOENCODING", None)
    pk = subprocess.run([sys.executable, RFG], input=json.dumps({"session_id": "rf-k", "cwd": E.tmp, "stop_hook_active": False,
                                                                 "transcript_path": E.transcript([("text", HANDBACK)])}).encode("utf-8"),
                        capture_output=True, env=env_k, timeout=60)
    ek = pk.stderr.decode("utf-8", errors="replace")
    check("(k) with no PYTHONIOENCODING the bounce text arrives intact, emoji included",
          pk.returncode == 2 and "'! 🟦 You <the action>'" in ek and "\ufffd" not in ek, ek[:160])
    E.mode("rf-l", "MODE: autopilot\nStage 1 handoff.")
    rc, _, e = E.rf("rf-l", E.transcript([("text", HANDBACK)]))
    check("(l) a hand-back with no orange line in autopilot bounces by default", rc == 2 and E.kinds("rf-l") == ["bounce"] and "one line now" in e)
    E.mode("rf-l2", "MODE: autopilot\nStage 1 handoff.")
    rc, _, _ = E.rf("rf-l2", E.transcript([("text", "```diff\n! 🟦 You paste the handoff into a fresh session.\n```\n"
                                                   "[16:40] done: batch 4 coded | running: none | blocked: none | next: successor | needs you: paste the handoff into a fresh session")]))
    check("(l) an autopilot handoff with the orange line and needs you: <action> passes", rc == 0 and E.kinds("rf-l2") == ["pass"])
    E.mode("rf-l3", "MODE: autopilot\nStage 1 handoff.")
    rc, _, _ = E.rf("rf-l3", E.transcript([("text", HANDBACK)]), cfg=E.cfg_shadow)
    check("(l) with autopilot_g1_bounce false the autopilot hand-back is shadow-logged only", rc == 0 and E.kinds("rf-l3") == ["shadow"])
    check.done()


def test_codex_paths(E, check):
    rc1, o1 = E.dispatch_codex("rf-i1", "cx-1", HANDBACK)
    rc2, o2 = E.dispatch_codex("rf-i2", "cx-2", HANDBACK_OK)
    check("(i) through the dispatcher, a Codex hand-back comes back as the block JSON; with the orange line it passes",
          rc1 == 0 and o1.get("decision") == "block" and str(o1.get("reason", "")).startswith("[REPLY FRAME] This reply hands the user")
          and "🟦 You <the action>" in o1.get("reason", "") and E.log("rf-i1")[0][0] == "bounce"
          and rc2 == 0 and o2 == {} and E.kinds("rf-i2") == ["pass"], f"{str(o1)[:160]} {o2}")
    check("a Codex stable Stop payload bounces without a transcript", E.codex("rf-stable", "turn-a") == 2)
    check("the same Codex turn is graded once against the kept bounced reply and never rearms",
          E.codex("rf-stable", "turn-a", ORANGE_FIX) == 0 and E.codex("rf-stable", "turn-a") == 0
          and E.kinds("rf-stable") == ["bounce", "retry-fixed"], str(E.kinds("rf-stable")))
    check("a new Codex turn rearms; an active continuation never bounces; an empty final is quiet",
          E.codex("rf-stable", "turn-b") == 2 and E.codex("rf-active", "turn-c", active=True) == 0 and E.codex("rf-empty", "turn-d", "") == 0)
    E.rfm.remember_codex_prompt({"session_id": "rf-peer", "prompt": "PEER_MESSAGE private payload"})

    def last(sid):
        k = E.kinds(sid)
        return k[-1] if k else ""
    check("the Codex machine flag stores no prompt text; an overlong machine turn is a G4 note",
          json.load(open(E.rfm.codex_prompt_path("rf-peer"), encoding="utf-8")) == {"machine": True, "bare": False}
          and E.codex("rf-peer", "peer-1", MACH2) == 0 and last("rf-peer") == "pass"
          and E.codex("rf-peer", "peer-2", MACH8) == 0 and last("rf-peer") == "note")
    E.rfm.remember_codex_prompt({"session_id": "rf-peer", "prompt": "[REPLY FRAME] continuation"})
    check("a continuation keeps the machine flag and sessions stay isolated",
          E.codex("rf-peer", "peer-3", MACH8) == 0 and last("rf-peer") == "note" and E.codex("rf-unrelated", "human-1", MACH8) == 0 and last("rf-unrelated") == "pass")
    E.rfm.remember_codex_prompt({"session_id": "rf-peer", "prompt": "Human follow-up"})
    check("a human follow-up clears the machine flag", E.codex("rf-peer", "human-2", MACH8) == 0 and last("rf-peer") == "pass")
    check("a Codex payload carrying raw UTF-8 emoji passes with no PYTHONIOENCODING (cp1252 stdin)",
          E.codex_raw("rf-cp", "cp-1", HANDBACK_OK, "check this") == 0 and E.kinds("rf-cp") == ["pass"])
    check("a Codex realtime spoken item ([COMPLETE] at byte zero) is exempt",
          E.codex_raw("rf-rt", "rt-1", "[COMPLETE] " + HANDBACK, "check this") == 0 and E.kinds("rf-rt") == ["exempt"])
    check("a Codex realtime inline item is checked without its directive",
          E.codex_raw("rf-ri", "ri-1", "::codex-realtime-inline{}\n" + HANDBACK_OK, "check this") == 0
          and E.codex_raw("rf-ri2", "ri-2", "::codex-realtime-inline{}\n" + HANDBACK, "check this") == 2)
    E.rfm.write_notes("rf-acc", ["first miss"])
    E.rfm.write_notes("rf-acc", ["second miss"])
    check("two noted replies before one prompt keep both notes", E.rfm.pop_notes("rf-acc") == ["first miss", "second miss"])
    check.done()


def test_live_positives_and_false_classes(E, check):
    """Next/ask lines from real hand-back misses (generalized), and the replay's false-positive classes."""
    live_pos = ["Next: nothing needs you; the [handoff](D:/x/HANDOFF.md) preserves approval, the exact exception, and the no-replay boundary.",
                "Next: nothing needs you; the successor continues from the [handoff](D:/x/HANDOFF.md).",
                "Next: nothing needs you; the successor starts with sealed source packet 11 after preflight.",
                "Next: nothing needs you; successor pickup is specified in the [handoff](D:/x/HANDOFF.md).",
                "[11:41] done: original bundle/checkpoint | running: none | blocked: fresh halt | next: scoped restart | needs you: approval",
                "- **If you want it back:** check Deleted files on the sync service's website. I can't see that list from here.",
                "I sent the command to open it; confirm you see it."]
    live_neg = ["```diff\n! Next: 🟦 You resume the handoff in a fresh session; existing approval carries forward.\n```",
                "Next: I open the page when it passes checks.", "I'll open it for you once the dataset is accepted.",
                "[10:00] done: x | running: none | blocked: none | next: check and open the results page | needs you: none",
                "- Last line: \"Needs you: <decision in plain words>\" or none.",
                "Sam has sent nothing since the \"please use this version\" email.",
                "The card reads 'please go to' above the link.",
                "[Open the summary report](D:/x/report.html)",
                "Next: nothing needs you; the watchdog relaunches a fresh session from the handoff.",
                "you could not see them because they sit in my working folder."]
    miss = [t for t in live_pos if not E.rfm.evaluate(t)[0]]
    fpos = [t for t in live_neg if E.rfm.evaluate(t)[0]]
    check("live positives bounce; the false classes (agent's own step, quotes, link labels, automatic relaunch) do not",
          not miss and not fpos, f"missed={miss} false={fpos}")
    check.done()


def test_round2_structural_layers(E, check):
    rc1, _, e1 = E.rf1("rf-m1", IMPER)
    rc2, _, _ = E.rf1("rf-m2", "Run 3 failed; the log names the missing column.")
    check("(m) a sentence-initial imperative to the user with an object bounces; the verb as a noun passes",
          rc1 == 2 and "start a fresh session" in e1 and rc2 == 0 and E.kinds("rf-m2") == ["pass"])
    rc, _, e = E.rf1("rf-n", "Two settings are wrong. Want me to apply the two fixes?")
    check("(n) 'Want me to apply the two fixes?' bounces", rc == 2 and "Want me to apply" in e)
    check("(o) 'Your call: CSV or .dta.' and 'your call from before still stands' bounce",
          E.rf1("rf-o1b", "The two formats are equally easy. Your call: CSV or .dta.")[0] == 2
          and E.rf1("rf-o2b", "\U0001f7e8 The export is ready; your call from before still stands.")[0] == 2)
    rc1 = E.rf1("rf-p1", "Removed the stray \U0001f7e6 marker. Paste this into a fresh session.")[0]
    rc2 = E.rf1("rf-p2", "```diff\n! Thank you for the data.\n```\nPaste this into a fresh session.")[0]
    rc3 = E.rf1("rf-p3", ORANGE_FIX + "\nPaste this into a fresh session.")[0]
    check("(p) a 🟦 mid-sentence or a diff '! Thank you' line no longer covers a paste instruction; '! 🟦 You paste' does",
          rc1 == 2 and rc2 == 2 and rc3 == 0)
    check("(q) a negated session clause passes", E.rf1("rf-q", "\U0001f7e8 The queue is complete; no successor session is needed.")[0] == 0)
    rc, _, e = E.rf1("rf-r", REC)
    check("(r) '🟨 Recommend CSV ...' bounces asking for the decision line, not the orange line",
          rc == 2 and e.startswith("[REPLY FRAME] This reply recommends between options the user owns (\"") and "no decision line" in e
          and "Write ONLY one line now, inside a diff fence: '- \U0001f7e7 Decision needed: <question>? Recommend: <option>; <tradeoff>'. Nothing else." in e
          and "You <the action>" not in e and E.log("rf-r")[0][1].startswith("G1 recommendation: '"), e[:200])
    rc1, _, _ = E.rf("rf-r", E.transcript([("tool", "Read"), ("text", REC), ("bounce", "x"), ("text", DEC_FIX)]))
    E.rf1("rf-r2", REC)
    rc2, _, _ = E.rf("rf-r2", E.transcript([("tool", "Read"), ("text", REC), ("bounce", "x"), ("text", "```diff\n! \U0001f7e6 You pick CSV.\n```")]))
    check("(r) the decision-line retry grades retry-fixed; an orange line instead is retry-still-wrong",
          rc1 == 0 and E.kinds("rf-r") == ["bounce", "retry-fixed"] and rc2 == 0 and E.kinds("rf-r2") == ["bounce", "retry-still-wrong"])
    s_bounced = "Handoff written. Paste this into a fresh session."
    rc0, _, e0 = E.rf1("rf-s", s_bounced)
    rc, _, _ = E.rf("rf-s", E.transcript([("tool", "Read"), ("text", s_bounced), ("bounce", "x"), ("text", "\U0001f7e8 The queue is complete; no successor is needed.")]))
    check("(s) the G1 text offers the 🟨 exit; a one-line 🟨 retry logs retry-declined and writes no note",
          rc0 == 2 and "If that phrase is not an action the user must take, write only: '\U0001f7e8 <one-line result>.'" in e0
          and rc == 0 and E.kinds("rf-s") == ["bounce", "retry-declined"] and not E.note("rf-s"))
    rc1, _, _ = E.rf("rf-t1", E.transcript([("text", LONG)], prompt="give me full detail on every setting"))
    rc2, _, _ = E.rf("rf-t2", E.transcript([("text", LONG)], prompt="status?"))
    E.rfm.remember_codex_prompt({"session_id": "rf-t3", "prompt": "Walk me through every setting, in detail."})
    rc3 = E.codex("rf-t3", "t3-1", LONG)
    check("(t) a long reply to a prompt asking for full detail gets no G2 note, on both hosts; to 'status?' it does",
          rc1 == 0 and E.kinds("rf-t1") == ["pass"] and rc2 == 0 and E.kinds("rf-t2") == ["note"] and rc3 == 0 and E.kinds("rf-t3") == ["pass"]
          and json.load(open(E.rfm.codex_prompt_path("rf-t3"), encoding="utf-8")).get("detail") is True)
    rc1, o1 = E.dispatch_codex("rf-u1", "cx-u1", IMPER)
    rc2, o2 = E.dispatch_codex("rf-u2", "cx-u2", REC)
    rc3, o3 = E.dispatch_codex("rf-u2", "cx-u2", DEC_FIX)
    check("(u) through the dispatcher as Codex, (m) blocks with the orange ask and (r) with the decision ask; the decision retry is retry-fixed",
          rc1 == 0 and o1.get("decision") == "block" and rc2 == 0 and o2.get("decision") == "block"
          and "Decision needed: <question>? Recommend: <option>; <tradeoff>" in str(o2.get("reason", ""))
          and rc3 == 0 and o3 == {} and E.kinds("rf-u2") == ["bounce", "retry-fixed"], f"{o1} {o2} {o3}")
    check.done()


def test_round3_negation_and_focus_view(E, check):
    rc1 = E.rf1("rf-w1", "Nothing is waiting on you.")[0]
    rc2 = E.rf1("rf-w2", "Yes. Everything you approved is shipped and checked. Nothing is waiting on you.")[0]
    rc3, _, e3 = E.rf1("rf-w3", "The upload is waiting on you.")
    rc4 = E.rf1("rf-w4", "Nothing gets edited until we both agree on the list and you say apply.")[0]
    check("(w) 'Nothing is waiting on you.' passes; 'The upload is waiting on you.' and a negator in an earlier sub-clause still bounce",
          rc1 == 0 and rc2 == 0 and rc3 == 2 and "waiting on you" in e3 and rc4 == 2, f"rc={rc1}/{rc2}/{rc3}/{rc4}")
    rc1, _, e1 = E.rf("rf-x1", E.transcript([("text", X_BOUNCED)]), settings=E.focus)
    rc2, _, _ = E.rf("rf-x1", E.transcript([("text", X_BOUNCED), ("feedback", "x"), ("text", ORANGE_FIX + "\n" + X_BOUNCED)]), settings=E.focus)
    check("(x) in focus view the bounce asks for the whole reply again; the full retry with the orange line is retry-fixed, no note",
          rc1 == 2 and "write the reply again in full with one added line first" in e1 and "'! 🟦 You <the action>'" in e1
          and "Write ONLY one line" not in e1 and rc2 == 0 and E.kinds("rf-x1") == ["bounce", "retry-fixed"] and not E.note("rf-x1"))
    E.rf("rf-x3", E.transcript([("text", X_BOUNCED)]), settings=E.focus)
    E.rf("rf-x3", E.transcript([("text", X_BOUNCED), ("feedback", "x"), ("text", X_BOUNCED)]), settings=E.focus)
    E.rf("rf-x4", E.transcript([("text", X_BOUNCED)]), settings=E.focus)
    E.rf("rf-x4", E.transcript([("text", X_BOUNCED), ("feedback", "x"), ("text", X_BOUNCED + " The tests pass.")]), settings=E.focus)
    E.rf("rf-x5", E.transcript([("text", X_BOUNCED)]), settings=E.focus)
    E.rf("rf-x5", E.transcript([("text", X_BOUNCED), ("feedback", "x"), ("text", "\U0001f7e8 The handoff is written.")]), settings=E.focus)
    check("(x) in focus view the identical retry and a lone 🟨 line log retry-declined; a retry still handing back is retry-still-wrong",
          E.kinds("rf-x3") == ["bounce", "retry-declined"] and E.kinds("rf-x4") == ["bounce", "retry-still-wrong"]
          and E.kinds("rf-x5") == ["bounce", "retry-declined"])
    rc6, _, e6 = E.rf("rf-x6", E.transcript([("text", REC)]), settings=E.focus)
    rc7, _, e7 = E.rf("rf-x7", "missing.jsonl", settings=E.focus, extra={"_codex_rollout": "modern.jsonl", "turn_id": "x7", "last_assistant_message": X_BOUNCED})
    rc8, _, e8 = E.rf("rf-x8", E.transcript([("text", X_BOUNCED)]), settings=os.path.join(E.tmp, "no_such_settings.json"))
    check("(x) the rec bounce has its focus variant; Codex and a missing settings file keep the one-line text",
          rc6 == 2 and "Decision needed: <question>?" in e6 and "write the reply again in full" in e6
          and rc7 == 2 and "Write ONLY one line now" in e7 and "Focus view" not in e7 and rc8 == 2 and "Write ONLY one line now" in e8)
    zt = E.transcript([("tool", "Read"), ("text", X_BOUNCED), ("feedback", "x"), ("text", ORANGE_FIX)], path=os.path.join(E.tdir, "rf_z.jsonl"))
    tz = E.rfm.parse_turn(zt)
    zt2 = E.transcript([("text", "Sending now."), ("feedback_other", "no tool ran"), ("text", ROUTINE)], path=os.path.join(E.tdir, "rf_z2.jsonl"))
    tz2 = E.rfm.parse_turn(zt2)
    E.rf1("rf-z", X_BOUNCED)
    rc = E.rf("rf-z", zt)[0]
    check("(z) the hook_additional_context entry counts as this hook's bounce or another hook's; the retry is retry-fixed",
          tz["own_bounces"] == 1 and tz["pre_bounce"] == X_BOUNCED and tz["final"] == ORANGE_FIX and tz["other_bounces"] == 0
          and tz2["other_bounces"] == 1 and tz2["pre_other"] == "Sending now." and tz2["own_bounces"] == 0
          and rc == 0 and E.kinds("rf-z") == ["bounce", "retry-fixed"])
    check.done()


def test_owner_name_is_configurable(E, check):
    env = base_env(E.state, CLAUDE_REPLY_FRAME_CONFIG=E.cfg, CLAUDE_SETTINGS_PATH=E.plain, HARNESS_OWNER_NAME="Dana")
    pl = {"session_id": "rf-own", "transcript_path": E.transcript([("text", "Next: Dana resumes from HANDOFF.md.")]), "cwd": E.tmp, "stop_hook_active": False}
    p = subprocess.run([sys.executable, RFG], input=json.dumps(pl), capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    check("HARNESS_OWNER_NAME names the person in the bounce text and in the phrase layer",
          p.returncode == 2 and "This reply hands Dana an action" in p.stderr, p.stderr[:200])
    check.done()
