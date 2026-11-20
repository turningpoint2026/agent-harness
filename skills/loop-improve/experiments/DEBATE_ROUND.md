# Experiment: debate round (Slock pattern) — priced test, pre-registered verdict

Status: RUN 2026-08-25 on a document-restructure round of a research project (one base workflow plus one debate workflow). VERDICT: PASS by the pre-registered rule (>=1 major verified entrant). Actuals: 12 agents (11 planned + verification), 1,223,485 raw Opus tokens (~1.22M Opus-eq, inside the 1.3-1.5M estimate), 18.3 min wall-clock; 25 turn-1 posts + 5 turn-2 new; 26 candidates -> 23 VERIFIED (18 major, 5 minor), 3 DISCREPANT killed. Entrants into the approved deliverable: 8 fix-package items (majors: undefined-term/naming, missing references, table-collision map, 9-block heading break, wrong repair template) + 11 empirical questions adopted into the next project meeting's agenda. Uplift attribution clean (all provenance-tagged debate:tN:debaterM). Debate is now an OPT-IN stage in SKILL.md per this verdict. Caveat: n=1 host round; the debate roamed beyond the round scope (baseline-spec findings), which was where much of its value came from - scope-width is a lever, not a bug, but price it. Origin: a podcast interview describing agents in a shared channel, no context share, iterating on
each other's findings. That version: unbounded, ~10x tokens. This version: capped, scored, disposable.

## Hypothesis
A capped debate round among reviewers surfaces VERIFIED ship-relevant findings that the loop's
isolated arms + verifiers miss, at a cost of ~1.3–1.5M Opus (~4–5% of a window).

## When to run
Exactly ONCE, grafted onto the next /loop-improve run whose round 1 completes normally. The
owner green-lights the graft at the round's token gate —
the stage is listed as its own line in the gate estimate.

## Design (uplift cleanly attributable)
1. Baseline runs UNCHANGED: arms → judge → verify → arbitrator. Ledger closed.
2. THEN the debate stage, seeded with (a) the judge's draft ON DISK and (b) the full findings
   ledger — debaters are told: everything in the ledger is KNOWN; only NEW findings count.
3. Channel mechanics (2 barriers, not free chat):
   - Turn 1: 5 debaters (Opus, parallel) each post ≤5 critiques with file+locator evidence.
   - Turn 2: each debater (parallel) reads the FULL turn-1 channel and must either (i) post a
     NEW finding that builds on or contradicts another debater's post, with evidence, or
     (ii) explicitly PASS. Bare agreement is banned — "+1" posts are the known sycophancy
     channel (a verifier that only judges plausibility tends to agree).
   - Moderator (Opus): dedups the channel against the ledger; outputs candidate NEW findings.
4. Every candidate goes through the SAME verification tier as normal claims (dual reader for
   numbers/citations, single adversarial for text). No shortcut into the deliverable.
5. Debaters share NOTHING but the channel (Slock's isolation rule = our blind-arm rule).

## Cost gate
Estimate: 11 Opus calls (5+5 debaters + moderator) x ~120–140k ≈ 1.3–1.5M Opus-equivalent
(~4–5% of a fresh window) [basis: v1.0.1 class costs, n=5 runs]. Stage aborts on the standard
2x overrun rule. Turn-2 context grows with the channel; cap each post at ~200 words.

## Pre-registered verdict (decided BEFORE running; do not relitigate after)
- Uplift = number of debate-originated findings that SURVIVE verification AND enter the final
  deliverable (owner-approved), counted from the moderator's provenance-tagged list.
- 0 verified entrants → debate is DROPPED; record actuals in DESIGN.md; delete this stage.
- ≥1 major or ≥3 minor verified entrants → debate becomes an OPT-IN stage in SKILL.md
  (off by default; priced in every gate).
- In between → the owner decides; either way the actuals go to DESIGN.md's calibration log.

## Workflow graft (drop into a round script after the Arbitrate phase)
```js
phase('Debate')
const seed = { draft_path: DRAFT_ON_DISK, known: arb.ledger }
const POST_SCHEMA = { type:'object', required:['posts'], properties:{ posts:{ type:'array',
  items:{ type:'object', required:['finding','evidence_file','locator'], properties:{
    finding:{type:'string'}, evidence_file:{type:'string'}, locator:{type:'string'},
    builds_on:{type:'string'} } } } } }
const t1 = await parallel([1,2,3,4,5].map(i => () => agent(
  `Debater ${i}. Read ${seed.draft_path}. KNOWN findings (do not repeat): ${JSON.stringify(seed.known)}.
   Post <=5 NEW critiques, each with evidence_file + locator. Angle ${i} of 5: pick a distinct lens
   (numbers, citations, logic, voice, structure by index). <=200 words per post.`,
  { label:'debate:t1:'+i, phase:'Debate', model:'opus', schema:POST_SCHEMA })))
const channel = JSON.stringify(t1.filter(Boolean))
const t2 = await parallel([1,2,3,4,5].map(i => () => agent(
  `Debater ${i}, turn 2. The channel so far: ${channel}. KNOWN: ${JSON.stringify(seed.known)}.
   Post a NEW finding that BUILDS ON or CONTRADICTS another debater's post (name it in builds_on),
   with evidence_file + locator — or return an empty posts array to PASS. Bare agreement is banned.`,
  { label:'debate:t2:'+i, phase:'Debate', model:'opus', schema:POST_SCHEMA })))
const mod = await agent(`Moderator. Channel turn 1: ${channel}. Turn 2: ${JSON.stringify(t2.filter(Boolean))}.
  KNOWN ledger: ${JSON.stringify(seed.known)}. Output only findings NOT in the ledger, deduped,
  each tagged with which debater/turn originated it.`,
  { label:'debate:moderator', phase:'Debate', model:'opus', schema:POST_SCHEMA })
// -> feed mod.posts into the standard Verify tier as new claims, provenance-tagged 'debate'.
```

## Measurement checklist
- [ ] Stage token actuals (budget.spent() delta across the phase, Opus-equivalent)
- [ ] Candidate findings posted (t1/t2 counts, pass rate at verification)
- [ ] Verified entrants into the final deliverable (the verdict number)
- [ ] Wall-clock of the stage
- [ ] Owner /usage readings at stage start + end (double as the pending ±10pp meter test)
