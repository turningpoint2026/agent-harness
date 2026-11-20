# /loop-improve: design notes

This is a condensed public version of the skill's private design log. The private log records every
calibration run by project name; here each run is reduced to the failure class it exposed and the rule
it produced. Numbers are kept where the private log measured them. Runs whose subject cannot be
described publicly are left out.

## Why each rule exists

- **v1.0** (2026-08-24): frozen after a test run on a claim-dense document package. Established: worker
  tier for every agent that reads or extracts, the top-tier model only for the judge and the arbitrator,
  verify after the judge picks content, a launch gate reported in percent of the usage window, a claims
  ledger.
- **v1.0.1** (2026-08-24, an audit by a parallel observer session): claim classes split before
  verification (6 of 10 non-VERIFIED verdicts were package-compliance claims sent to source verifiers,
  so compliance now goes to a deterministic sweep); a progress line after every phase (the owner had to
  ask for progress); a pre-launch lint (one missing output schema cost one crash and one wasted judge
  run); a diff guard on micro-rounds; scope rule "one convergence point".
- **v1.0.2** (2026-08-25, three loops running at once in one window): a shared window ledger; the gate
  informs and never blocks (the owner may hold capacity the agent cannot see); re-gate at every phase
  boundary.
- **v1.0.3** (2026-08-25, a 3.6x cost overrun and an accounting audit): a source-token cost law (an
  agent costs about 50k plus 4.8 times the source tokens it is assigned; flat per-agent classes had
  under-priced multi-document agents 3 to 9 times); one token series per number, never mixed;
  error-compounding re-gate (a phase more than 2x over re-prices every remaining phase);
  tier-conservative harvesting (60 percent of harvested claims came back overstated without it);
  entity-keyed deduplication (label keys inflated a blind arm's unique findings about 20 times).
- **v1.0.4** (2026-08-25, a media-gallery run whose problems were all step-0 gates, with no agent
  disobedience): a required READER stage between judge and ship. The owner's diagnosis: "a
  default-skeptical verifier will wave through unlimited true uselessness." Also: every rubric opens
  with genre and audience; sample before scale; render-check on generated media; the top-tier model
  for judgment only; report only closed-phase numbers; PREFLIGHT and RUBRIC templates so compliance is
  an artifact on disk, not a prose rule.

## Concept

A general iterative improvement loop for a deliverable worth more than one draft. Engine: Claude Code's
Workflow tool (deterministic rounds, fan-out, caching, resume). Conventions: the orchestrate-workers
skill (folder layout, self-contained briefs, audit files). Task-specific part: a short rubric per task
type (what "better" means, stop criteria, banned patterns).

## Fixed core sequence

1. Read project memory; freshness check (list candidate versions with mtimes, declare the canonical one).
2. Scope contract and plan shown to the owner before any files.
3. Token estimate gate before every workflow launch.
4. Round N: prep, reviewer arms with different visibility, verification, judge draft, reader.
5. Persist every phase output to the project folder at once; the workflow cache dies with the session.
6. The owner reads; comments land in the next round's notes file.
7. Stop on wording-only feedback, on a round that adds fewer than one surviving major finding, or when
   the arbitrator calls it.
8. Ship: humanizer pass, render, verify.

## Verification design

- A verifier asked only whether a claim is plausible tends to agree with it. The working
  design: (a) blind extraction before the verifier sees the claim, (b) an adversarial reader whose
  default verdict is DISCREPANT, (c) a top-tier arbitrator on disagreement.
- With that frame, a later run using a cheap blind-extraction lane surfaced 11 disputes that
  arbitration repaired, and no sycophancy was observed.
- An arbitrator overturned 2 verifier verdicts by opening the cited primary log the verifier had
  skipped. Arbitrator prompts must require reading the cited primary source, not adjudicating from the
  verifier's quotes.
- The judge ranks by cross-agent convergence, so numeric findings crowd out framing findings. Reserve
  slots per category.
- The judge drafts only from the verified ledger; it never inherits a number it did not verify.

## Arbitration balance

A finding that flips twice or splits verifiers twice goes to an arbitrator instead of another round:
tier 1 is the top-tier model at maximum effort, tier 2 is the owner through a plain-language decision
question. No finding gets more than two refuter pairs in total.

## Cost and calibration

- A test run of 19 agents (17 worker-tier, a top-tier judge and arbitrator) cost at most 2.11M tokens,
  was approved in one feedback pass, and the owner found zero claim errors. The worker/judge model split
  carried most of the saving.
- Write the judge's draft to disk before verification: 5 of 5 UNVERIFIABLE verdicts in one run were
  claims about a file that existed only in workflow state, and all passed once checked against the
  written file.
- Find feedback by diffing the reviewed file against a pristine copy, never by searching for comment
  markers: 1 of 4 comments in one round was unmarked.
- Judges that write bulk prose need a source term and an output term in the estimate: one gallery
  judge stage ran 12 to 24 times over its estimate.
- Extract-once held: pinning agents to a 69k-token source bundle built mechanically in the main
  session, and forbidding them to walk the file tree, avoided the overrun shape seen earlier.
- Precision application of arbitrated wording is main-session work. An applier agent stalled at its
  watchdog having written nothing; the 15 corrections were then applied by hand with zero errors.
- In a language-polish run, judges marked 141 of 284 records as needing the owner, far above what the
  owner will rule on. A mechanical re-triage on three owner rules cut that to 56.

## Defect: claim-id collision across arms (cost one full round)

Four discovery arms were each told to emit claims with ids, and each independently used C01 to C25.
The script keyed its ledger on the claim id, so 100 claims collapsed into 25 slots, last writer winning.
The verifier agents noticed the duplicate ids and renamed them on their own initiative, so about 200
verifier judgments bound to nothing. The judge received 25 arm-ambiguous rows labeled as the verified
ledger. Detection was only the run result's 100 claims in against 25 surviving, a silent 75 percent loss
that looked like strict verification. Recovery was positional, from the run journal. Candidate rules:
the harness namespaces claim ids on collection and never keys a ledger on an id an agent chose; assert
that every returned id matches a known claim and that claims in equals claims ledgered.

## Reader stage

On its first frozen run the reader returned 42 skip items, 41 missing items and 18 nearly-cut-but-kept
items, and all 18 kept items were trust apparatus (citations, caveats, coverage warnings). The carve-out
list in SKILL.md exists so a utility pass cannot optimize honesty away. On a later planning run the
reader found 15 build blockers in a plan that read as finished.

## Other recorded facts

- A render-check pass that opened 193 generated stills found 27 percent caption accuracy before
  correction.
- Usage-limit trips kill agents mid-run with misleading failure labels; a failed agent's artifacts are
  not disproven claims.
- A top-tier arbitrator was refused twice by the provider's safety filter on ordinary planning content;
  the claims kept their adversarial verdicts. A fallback rule is a candidate.
