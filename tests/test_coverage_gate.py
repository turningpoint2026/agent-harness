"""Synthesis coverage gate, read ledger and the user-only bypass listener (2026-08-06 design)."""
import hashlib
import json
import os

from conftest import hook, load_module, run_hook

GUARD = hook("synthesis_coverage_guard.py")
LEDGER = hook("read_ledger.py")
LISTENER = hook("coverage_bypass_listener.py")
SYNTH_CONTENT = "x" * 500  # over MIN_NAME_TRIGGER_CHARS, no findings language


def write_payload(fp, content, sid):
    return {"session_id": sid, "tool_name": "Write", "tool_input": {"file_path": fp, "content": content}}


def test_coverage_gate(tmp_path, state, check):
    tmp = str(tmp_path)
    proj = os.path.join(tmp, "000PROJECT_TEST")
    os.makedirs(proj)
    open(os.path.join(proj, "Claude_ProjectMemory_TEST.md"), "w").write("mem\n")
    doc_a = os.path.join(proj, "alpha_findings_report.md")
    doc_b = os.path.join(proj, "beta_results.md")
    open(doc_a, "w").write("finding A\n")
    open(doc_b, "w").write("finding B\n")
    target = os.path.join(proj, "summary_for_faculty.md")

    # read ledger
    rc, _, _ = run_hook(LEDGER, {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": doc_a}}, state)
    rc2, _, _ = run_hook(LEDGER, {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": doc_b, "limit": 50}}, state)
    led = os.path.join(state, "read_ledger_s1.jsonl")
    entries = [json.loads(line) for line in open(led)] if os.path.isfile(led) else []
    check("ledger: full read logged", rc == 0 and any(e["full"] and e["path"].endswith("alpha_findings_report.md") for e in entries))
    check("ledger: limited read logged as not-full", rc2 == 0 and any(not e["full"] and e["path"].endswith("beta_results.md") for e in entries))

    # gate: non-project file passes
    rc, _, _ = run_hook(GUARD, write_payload(os.path.join(tmp, "loose_report.md"), SYNTH_CONTENT, "s2"), state)
    check("gate: non-project file passes", rc == 0)

    # tripwire mode (no CORPUS.md)
    rc, _, err = run_hook(GUARD, write_payload(target, SYNTH_CONTENT, "s3"), state)
    check("gate tripwire: first write blocked", rc == 2)
    check("gate tripwire: block enumerates corpus", "alpha_findings_report.md" in err and "beta_results.md" in err, err[:200])
    rc, _, _ = run_hook(GUARD, write_payload(target, SYNTH_CONTENT, "s3"), state)
    check("gate tripwire: retry passes (once per session)", rc == 0)

    # manifest mode
    open(os.path.join(proj, "CORPUS.md"), "w").write(
        "# Corpus\n- alpha_findings_report.md\n- beta_results.md\n- ghost_missing.md\nprose line without extension\n")
    rc, _, err = run_hook(GUARD, write_payload(target, SYNTH_CONTENT, "s4"), state)
    check("gate manifest: blocked with empty ledger", rc == 2)
    check("gate manifest: lists both unread files", "alpha_findings_report.md" in err and "beta_results.md" in err)
    check("gate manifest: reports missing manifest entry", "ghost_missing.md" in err)
    run_hook(LEDGER, {"session_id": "s4", "tool_name": "Read", "tool_input": {"file_path": doc_a, "limit": 10}}, state)
    rc, _, err = run_hook(GUARD, write_payload(target, SYNTH_CONTENT, "s4"), state)
    check("gate manifest: partial read still blocked", rc == 2 and "alpha_findings_report.md" in err)
    run_hook(LEDGER, {"session_id": "s4", "tool_name": "Read", "tool_input": {"file_path": doc_a}}, state)
    run_hook(LEDGER, {"session_id": "s4", "tool_name": "Read", "tool_input": {"file_path": doc_b}}, state)
    rc, _, err = run_hook(GUARD, write_payload(target, SYNTH_CONTENT, "s4"), state)
    check("gate manifest: passes after full corpus read", rc == 0, err[:200])
    rc1, _, _ = run_hook(GUARD, write_payload(target, SYNTH_CONTENT, "s5"), state)
    rc2, _, _ = run_hook(GUARD, write_payload(target, SYNTH_CONTENT, "s5"), state)
    check("gate manifest: blocks on EVERY attempt (no retry escape)", rc1 == 2 and rc2 == 2)

    # bypass listener + gate honoring the flag
    rc, out, _ = run_hook(LISTENER, {"session_id": "s5", "prompt": "please override coverage gate for this one"}, state)
    check("listener: phrase creates flag", rc == 0 and os.path.isfile(os.path.join(state, "coverage_bypass_s5.flag")))
    check("listener: confirmation injected", "bypass recorded" in out.lower())
    rc, _, _ = run_hook(GUARD, write_payload(target, SYNTH_CONTENT, "s5"), state)
    check("gate: user bypass flag honored", rc == 0)
    rc, _, _ = run_hook(LISTENER, {"session_id": "s6", "prompt": "normal prompt"}, state)
    check("listener: no flag without phrase", rc == 0 and not os.path.isfile(os.path.join(state, "coverage_bypass_s6.flag")))

    # skip names and the content trigger
    rc, _, _ = run_hook(GUARD, write_payload(os.path.join(proj, "CORPUS.md"), SYNTH_CONTENT, "s7"), state)
    check("gate: CORPUS.md itself not gated", rc == 0)
    findings_prose = ("Our findings indicate strong effects. We found that X. The results show Y. The data show Z. ") * 30
    rc, _, _ = run_hook(GUARD, write_payload(os.path.join(proj, "notes_for_team.md"), findings_prose, "s8"), state)
    check("gate: content trigger fires on findings language, non-synth name", rc == 2)
    rc, _, _ = run_hook(GUARD, write_payload(os.path.join(proj, "notes_tiny.md"), "short findings note", "s9"), state)
    check("gate: short content not gated", rc == 0)

    # freshness: a read certifies the content it hashed
    os.environ["CLAUDE_HOOK_STATE_DIR"] = state
    try:
        scg = load_module("synthesis_coverage_guard_t", GUARD)
        good = hashlib.sha256(open(doc_a, "rb").read()).hexdigest()
        with open(os.path.join(scg.state_dir(), "read_ledger_frz.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"path": doc_a.replace("\\", "/"), "full": True, "sha256": good}) + "\n")
            f.write(json.dumps({"path": doc_b.replace("\\", "/"), "full": True, "sha256": "0" * 64}) + "\n")
            f.write(json.dumps({"path": target.replace("\\", "/"), "full": True, "via": "codex-shell"}) + "\n")
        have = scg.load_full_reads("frz")
    finally:
        os.environ.pop("CLAUDE_HOOK_STATE_DIR", None)
    check("gate freshness: a stale hash and an unhashed Codex shell read are not credited; a matching hash is",
          doc_a.replace("\\", "/") in have and doc_b.replace("\\", "/") not in have and target.replace("\\", "/") not in have, str(have))
    check.done()
