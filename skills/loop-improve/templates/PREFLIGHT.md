# PREFLIGHT — <run name> (<date>)
Copy this file into `<project>\_loop_work\PREFLIGHT_<runname>.md` at step 0 and fill EVERY line
before launch. An unfilled line is a visible compliance gap — the observer audits this file from
disk. Do not delete lines; write "n/a + why" if a line genuinely does not apply.

## Step 0 — orient
- [ ] Project memory / SESSION_STATE read: <files>
- [ ] Freshness check done; canonical target: <path> (mtime <..>)
- [ ] GENRE + AUDIENCE: this deliverable is a <genre>; the reader will use it to <action>.
      (Asked the owner? <yes/turn ref | known from: ...>)
- [ ] Rubric: <existing rubrics/<name>.md | fresh, approved with plan>
- [ ] SAMPLE BEFORE SCALE: parameters applied at scale: <list or "none">.
      One rendered sample shown to the owner: <link/turn | n/a>

## Gate (api-raw units)
- [ ] Estimate: <N>M api-raw = base 50k/agent + 4.8 x assigned source tokens (+ judge source term)
- [ ] Window: ledger read (last rows), peer-session inventory checked; other consumers active: <who | none>
      Presented as: <fresh window | COMBINED load on ~X% used>
- [ ] Experiments folder checked; PENDING specs surfaced as opt-in lines: <list | none>
- [ ] Ledger LAUNCH row appended (series column filled)
- [ ] Launch lint: structured-return schema where supported / models explicit / actual agent,
      context and reasoning recorded / highest selected reasoning on judge-arbitrator stages /
      phase: on every stage / judge output written to disk before Verify / progress log-line
      after every phase / chain ends on work not report

## Close checklist (fill at run close — run is NOT closed until all checked)
- [ ] READER stage ran (genre statement as input); cuts applied: <n> / over-reach noted: <..>
- [ ] Ledger CLOSE row written (closed-phase numbers only — no in-flight spend)
- [ ] Actuals vs estimate + any new lesson appended to DESIGN.md calibration log
- [ ] Run record / STATE file complete in _loop_work
