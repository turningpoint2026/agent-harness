"""Claims-ledger gate: claim-shaped prose lines in a deliverable must name a source or carry a label."""
import json
import os

from conftest import hook, run_hook

CLAIMS = hook("claims_ledger_gate.py")
LISTENER = hook("coverage_bypass_listener.py")
PAD = "\nRoutine prose with no assertions in it at all.\n" * 12
MONEY = "The firm paid $25,500 for the pickup truck in July."


def test_claims_ledger_gate(tmp_path, state, check):
    tmp = str(tmp_path)
    proj = os.path.join(tmp, "000PROJECT_TEST")
    os.makedirs(proj)
    open(os.path.join(proj, "Claude_ProjectMemory_TEST.md"), "w").write("mem\n")
    rpt = os.path.join(proj, "team_report.md")

    def claims(content, sid="c1", fp=rpt, tool="Write"):
        key = "content" if tool == "Write" else "new_string"
        return run_hook(CLAIMS, {"session_id": sid, "tool_name": tool, "tool_input": {"file_path": fp, key: content}}, state)

    # scope
    check("claims: non-deliverable basename passes", claims("The firm paid $25,500 for it." + PAD, "c1", os.path.join(proj, "random_thoughts.md"))[0] == 0)
    check("claims: outside a project root passes", claims("The firm paid $25,500 for it." + PAD, "c1", os.path.join(tmp, "loose_summary.md"))[0] == 0)
    check("claims: short content passes", claims("The firm paid $25,500.", "c1")[0] == 0)
    check("claims: ledger/scratch names skipped", claims("The firm paid $25,500 for it." + PAD, "c1", os.path.join(proj, "claims_ledger_report.md"))[0] == 0)

    # the three claim shapes, unsourced
    rc, _, err = claims(MONEY + PAD, "c2")
    check("claims: unsourced money blocked", rc == 2 and "[number]" in err, err[:200])
    rc, _, err = claims("Written by Jane Doe and revised twice." + PAD, "c3")
    check("claims: fabricated byline blocked as attribution", rc == 2 and "[attribution]" in err, err[:200])
    rc, _, err = claims("The package was sent to the editor last week." + PAD, "c4")
    check("claims: unsourced status assertion blocked", rc == 2 and "[status]" in err, err[:200])
    check("claims: unsourced percentage-style figures blocked", claims("The treatment-control gap is 20.9 versus 11.1." + PAD, "c5")[0] == 2)

    # accepted escapes
    check("claims: inline (source: ...) passes", claims(MONEY[:-1] + " (source: bill_of_sale.pdf)." + PAD, "c6")[0] == 0)
    check("claims: [UNVERIFIED] label passes", claims(MONEY[:-1] + " [UNVERIFIED: no receipt on disk]." + PAD, "c7")[0] == 0)
    check("claims: bare filename mention passes", claims("The gap persists at 20.9 percent, per crosswalk.csv row 44." + PAD, "c8")[0] == 0)
    check("claims: source on the following line passes",
          claims("Quarterly revenue rose to $4.2M in the third quarter.\n  (source: SEC 10-K, verified via WebFetch)" + PAD, "c9")[0] == 0)
    check("claims: page anchor counts as a source", claims("The company has made its product in Ohio since 1896, p.81." + PAD, "c9b")[0] == 0)

    # false-positive guards
    rc, _, err = claims("See section 3 and step 12 on page 240, rule 7, v2.1.0, at 10:30 on 2026-08-09." + PAD, "c10")
    check("claims: structural refs, dates and times not claims", rc == 0, err[:300])
    check("claims: headings not scanned", claims("## Results for 1,251 figures\n" + PAD, "c11")[0] == 0)
    check("claims: fenced code not scanned", claims("```\ntotal = 1,251 rows at $25,500\n```" + PAD, "c12")[0] == 0)
    check("claims: SUGGEST blocks not scanned", claims("### >>> SUGGEST clarity\nWe found 1,482 firms.\n### <<<" + PAD, "c13")[0] == 0)
    check("claims: blockquotes not scanned", claims("> Quoted: they paid $25,500 for it.\n" + PAD, "c14")[0] == 0)
    tbl = "| firm | exits | share |\n|---|---|---|\n| Acme | 1,482 | 31.7% |\n"
    check("claims: tables are data, not scanned", claims(tbl + PAD, "c15")[0] == 0)
    check("claims: numeric-dense stat rows not scanned",
          claims("Coefficients: -0.012 -0.058 0.134 0.003 1,482 6,058 0.646" + PAD, "c15b")[0] == 0)

    # provenance header waives numbers, never attribution
    prov = "Source: panel_build.csv (2026-08-02 build)\n\n"
    check("claims: provenance header waives NUM", claims(prov + MONEY + PAD, "c15c")[0] == 0)
    rc, _, err = claims(prov + "Written by Jane Doe and revised twice." + PAD, "c15d")
    check("claims: provenance header does NOT waive attribution", rc == 2 and "[attribution]" in err, err[:200])

    # attribution precision (the 2026-08-09 dry-run false-positive classes)
    check("claims: document referents are not attributions", claims("Chapter says the firm moved production to Vietnam." + PAD, "c15e")[0] == 0)
    check("claims: a real named source still fires", claims("Doe said the firm moved production to Vietnam." + PAD, "c15f")[0] == 2)
    check("claims: 'sorted by X Y' is not a byline", claims("Results were sorted by Best Seller rank in the listing." + PAD, "c15g")[0] == 0)

    # gate behaviour
    rc1, _, _ = claims(MONEY + PAD, "c17")
    rc2, _, _ = claims(MONEY + PAD, "c17")
    check("claims: blocks on EVERY attempt (no retry escape)", rc1 == 2 and rc2 == 2)
    rc, out, _ = run_hook(LISTENER, {"session_id": "c17", "prompt": "fine, override claims gate"}, state)
    check("listener: claims phrase creates flag and confirms",
          rc == 0 and "claims-gate" in out and os.path.isfile(os.path.join(state, "claims_bypass_c17.flag")))
    check("claims: user bypass flag honored", claims(MONEY + PAD, "c17")[0] == 0)
    run_hook(LISTENER, {"session_id": "c18", "prompt": "override coverage gate"}, state)
    check("claims: coverage bypass does NOT silence claims gate", claims(MONEY + PAD, "c18")[0] == 2)
    check("claims: Edit payloads scanned too", claims(MONEY + PAD, "c19", tool="Edit")[0] == 2)

    # .tex support (2026-08-28)
    texf = os.path.join(proj, "paper.tex")
    check("claims tex: cited prose passes", claims("Prior work finds a 27.5 percent effect \\citep{smith2020}." + PAD, "t1", fp=texf)[0] == 0)
    check("claims tex: unsourced number blocked", claims("The firm spent 4.2 million dollars on lobbying in 2019." + PAD, "t2", fp=texf)[0] == 2)
    check("claims tex: comments and tables skipped",
          claims("% comment: they paid $25,500\n\\begin{table}\nAcme & 42.7 & 19.3 \\\\\n\\end{table}" + PAD, "t3", fp=texf)[0] == 0)
    check.done()


def test_claims_gate_handoff_status_class(tmp_path, state, check):
    """Handoffs under a registered root are gated for STATUS claims only; the proof is the check that made them."""
    tmp = str(tmp_path)
    snaproot = os.path.join(tmp, "snap_root")
    os.makedirs(snaproot)
    reg = os.path.join(tmp, "snapshot_roots_t.json")
    json.dump({"version": 1, "roots": [{"path": snaproot, "slug": "t"}]}, open(reg, "w"))
    pad = "\n".join("Context line %d about the campaign layout and the files on disk, nothing asserted here." % i for i in range(8))
    h_bad = (pad + "\n\n## Position\n\nThe batch-12 worker is still running as PID 32012 and its journal is being written every tick.\n"
             "The item-4 ledger was applied and the backup dir was written before the merge.\n")
    h_ok = (pad + "\n\n## Position\n\nThe batch-12 worker is still running as PID 32012 and its journal is being written every tick "
            "(checked 14:02 via tasklist | findstr 32012).\nThe item-4 ledger was applied and the backup dir was written before the merge "
            "[UNVERIFIED: not re-checked since 13:40].\n")

    def claims_h(fp, content, sid="h1"):
        rc, _, err = run_hook(CLAIMS, {"session_id": sid, "tool_name": "Write", "tool_input": {"file_path": fp, "content": content}},
                              state, CLAUDE_SNAPSHOT_ROOTS=reg)
        return rc, err

    rc1, e1 = claims_h(os.path.join(snaproot, "_BATCH12_HANDOFF_2026-09-06.md"), h_bad)
    rc2, _ = claims_h(os.path.join(snaproot, "_BATCH12_HANDOFF_2026-09-06.md"), h_ok)
    rc3, _ = claims_h(os.path.join(snaproot, "notes_position.md"), h_bad)
    rc4, _ = claims_h(os.path.join(tmp, "elsewhere", "_BATCH12_HANDOFF.md"), h_bad)
    rc5, _ = claims_h(os.path.join(snaproot, "_RESUME_NEXT_SESSION.md"), h_bad)
    check("handoff: 'PID 32012 running' with no token blocks, STATUS class, names the check form",
          rc1 == 2 and "[status]" in e1 and "PID 32012" in e1 and "(checked" in e1 and "[number]" not in e1, f"rc={rc1} " + e1[:200])
    check("handoff: '(checked HH:MM via <command>)' and an [UNVERIFIED] label pass", rc2 == 0, f"rc={rc2}")
    check("handoff: a non-handoff file in the same folder and a handoff outside every root are not gated", rc3 == 0 and rc4 == 0)
    check("handoff: a RESUME card is handoff-shaped too", rc5 == 2)
    check.done()
