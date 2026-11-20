---
name: loop-improve
description: "Iterative multi-agent improvement loop for a deliverable worth more than one draft: reviewers, claim verification, judge, owner feedback per round."
---

# /loop-improve

**Voice pass (binding, harness redesign step 8, 2026-09-06):** before presenting any draft in the owner's voice, run the humanizer checklist against `<HARNESS_ROOT>/hooks/VOICE_RULES.md` (the single home of the banned word list; not included in this repository) and report it in one line: `voice pass: N changes`.

**Mistake registers (binding):** during PREFLIGHT, read `<HARNESS_ROOT>/registers/_REGISTERS_INDEX.md` (a per-task-type list of logged mistakes, each with the owner's ruling; the registers themselves are private), route to the register(s) matching the deliverable's task type, cite them in every worker prompt and rubric, and append new lessons (with owner rulings; CANDIDATE otherwise) at close-out. Unsure which register fits: ask the owner before launching.

**v1.0.4** (2026-08-25; consolidated same day — every rule below was bought with an observed incident; the incident record and version history live in `DESIGN.md`, read it before weakening any rule). The loop exists to (a) never skip process steps, (b) iterate instead of one-shotting, (c) never ship an unverified claim, (d) never ship a true-but-useless one.

**Scope**: N sub-items are fine ONLY when they converge into ONE reviewable package — one judge, one claims ledger, one approval surface for the owner. Independent multi-artifact production → `/orchestrate-workers`. Composing the two = "RAs first, editor second": workers produce + audit the analysis artifacts, then this loop writes the document about them, with the workers' audited reports as pinned read-only sources. Sequence, never nest.

## 0. Orient (main session, before any agents)
- Copy `templates/PREFLIGHT.md` into `_loop_work\` and fill it as you go — an unfilled line is a visible compliance gap, audited from disk.
- Read `Claude_ProjectMemory_*.md` / `SESSION_STATE*.md`. Freshness check: list candidate versions with mtimes, declare canonical (CLAUDE_GLOBAL rule 6).
- Create `<project>\_loop_work\` (name must not contain "shared"); extract sources to text there.
- Rubric: load from `rubrics/` (task-specific rubrics are not included in this repository), or draft fresh from `templates/RUBRIC_TEMPLATE.md`. **Every rubric OPENS with a genre + audience statement** (what the document IS, what the reader will DO with it — ask the owner if unknown); takeaway-first entry formats.
- **Sample before scale**: any parameter applied across many items (clip length, layout, entry format) → ONE rendered sample to the owner before mass production. Items that vary in kind → presentation form is a per-item agent decision, not a harness constant. Generated media gets a render-check pass (an agent OPENS the artifact and checks it against its own caption; 8/25: 27% caption accuracy on a media gallery before this pass).
- Locate voice/convention samples for the judge.

## 1. Plan + scope contract (show the owner, wait for go)
- ≤10 lines: round structure, arms + visibility, verification tiers, output files, stop rule. Fresh rubric may be approved in the same turn as the plan.
- Ask for hunches / must-check items — they become binding reviewer inputs.

## 2. Token gate (EVERY launch, including resumes with new stages)
- **Units = api-raw tokens** (in + out + cache_write; cache reads ≈ unmetered). Per-agent cost = ~50k base + **~4.8× its assigned SOURCE TOKENS**; judges add a source term for what they re-read and an output term for bulk prose. Window: **100% ≈ 21–26M api-raw (~0.25M/pp)**. Every recorded number states its series (api_usage | harness_agg | budget_spent_output — NEVER mix; no constant ratio). `scripts/sum_usage.py <session-dir> --row S RUN EVENT` emits the api_usage ledger row.
- **The gate INFORMS, never blocks.** ≤~25% of a fresh window: auto-launch. Above: present estimate + phase breakdown + trimmed variant as a plain decision-ask and WAIT. Never refuse on own judgment — the owner may hold off-book capacity.
- **Concurrency (pull-based, at PLAN time)**: read `state\WINDOW_LEDGER.csv` recent rows + observer-log tail + ListAgents. Another consumer active → present COMBINED load, and re-gate at every phase boundary (cache boundary = the AGENT: holding at a boundary is free, killing mid-phase forfeits in-flight agents; the biggest unspent line — usually a Fable stage — is the tail risk).
- **Ledger rows** (columns: timestamp_local, session, run_id, event, series, raw_tokens, cache_read, opus_eq, note) at launch, each phase close, run close. The close row is part of the close checklist — no row, run not closed.
- **Estimate-error compounding**: any phase >2× over → re-price ALL remaining phases by the observed ratio before opening the next.
- **Only CLOSED-phase numbers are reportable**; running spend is always labeled IN-FLIGHT.
- **Experiments**: check `experiments\`; PENDING specs get their own opt-in gate line, run only on the owner's explicit yes, follow their pre-registered verdict, then are marked RUN — never silently re-run.
- **Models** (set `model:` on EVERY agent call — unset inherits the session model, which is how Fable leaks):
  - ⚠️ **FABLE NEVER REACHES A WORKER** (owner standing order; Fable is the top-tier model, Opus the worker tier). Arbitrator: always Fable `effort:'max'`. Judge: Fable only when judgment-dense; **Opus when its input is pre-digested or its output is bulk prose**. All workers: `model:'opus'` or lower; recommend Sonnet/Haiku for mechanical stages in the plan ("stage X is mechanical, saves ~Y%").
  - Never change a stage's model mid-run (cache invalidation); a fresh run id has no cache to protect.
- **Script requirements**: `log()` the estimate first; after EVERY `phase()`: `log('Phase <name> done - spent <budget.spent()> (<pct of estimate>%), phase <k>/<K>')` — this is the owner's progress view and the 2×-overrun trigger. Backgrounded chains END ON THE WORK, not on a reporting command. Calibration `/usage` readings are ASKED of the owner at launch and close, not hoped for.
- **Pre-launch lint**: every `agent()` has `schema:`; models explicit; `effort:'max'` on Fable stages; `phase:` on every stage; judge output written to disk before Verify.
- After a limit trip: save state, probe with one trivial agent before resuming. Failed-agent artifacts are NOT disproven claims.

## 3. Round N workflow (one script, resumable)
- **Prep**: context ledger(s), old-vs-new diff if revision, voice note, third-party source audits.
- **Extractor/harvest prompts demand tier-conservative claims** (claim the lowest defensible tier; verification upgrades — 60% came back inflated without this).
- **Cross-arm overlap metrics use ENTITY keys** (person/section + location + overlapping span), never name/label keys (label keys inflated blind-arm uniqueness ~20×).
- **Arms**: A sees everything; B blind (artifact only). Reserve finding slots per category so numeric findings don't crowd out theory/framing.
- **Verification** (claim-level):
  - Split by claim class FIRST: source-fact claims → agent verifiers; **package-compliance claims → deterministic mechanical sweep on the written file, never source-reading agents**.
  - Numbers/citations: TWO readers (blind extraction + adversarial default-DISCREPANT). Text/structural: ONE adversarial reader. Never plausibility-judging refuters (they tend to agree: sycophancy).
  - Blind extractors may launch at Prep time (claim-agnostic); only adversarial readers need ship_claims.
  - Disagreement → Fable-max arbitrator. Max 2 refuter pairs per finding across all rounds.
  - **OPT-IN debate stage** (validated; spec in `experiments/DEBATE_ROUND.md`): after judge output is on disk, 5 lens-diverse Opus debaters × 2 turns + moderator; own gate line; candidates enter only through normal verification, provenance-tagged 'debate'. Strongest when round scope is narrow — debaters roam the whole document.
- **Voice/style screen per draft, not ship-only**: judge/draft briefs embed the anti-AI banned list + the owner's tells (no colon-setup, no figurative compression, no em dashes); mechanical banned-list grep on EVERY draft — agent or micro-round — before the owner sees it (construct terms exempt). Ship-time humanizer stays as final gate, not the only one. Note: python-via-shell edits bypass the prose hook, so this sweep is the only screen on that path.
- **Judge** (drafts ONLY from the verified ledger, applies corrected wordings, voice note, slot limits; outputs draft + judgment-call notes + questions for the owner). Judge draft goes to DISK before Verify.
- **READER stage — REQUIRED between judge and ship (the utility axis)**: one agent, ONLY the deliverable + genre/audience statement. No sources, no fact-challenging. Two questions: what would the reader SKIP; what is MISSING before acting. HARD enumerated carve-out — never cut: citation, tier/evidence label, honesty caveat, thin-coverage warning, not-in-source statement, methodology note, considered-and-rejected record ("the sentences a reader skips are the sentences that let him calibrate trust" — a utility axis with no floor optimizes into dishonesty). REQUIRED outputs: skip list, missing list, `nearly_cut_but_kept` (over-reach is invisible without it). Runs after the judge because the failure is emergent at document level. The missing list is where it earns its cost.
- Persist every phase output to `_loop_work\` immediately (`ROUND_N.md`, `RAW_*.json`); cache dies with the session.

## 4. Feedback + next round
- The owner reads `ROUND_N.md` §draft; comments land in `ROUND_(N+1)_notes.md`.
- Discover comments by DIFFING the reviewed file against a pristine copy, never by grepping for $$$ markers (unmarked comments are common).
- Substance comment with NO new checkable claims → main-session micro-round; **checked, not asserted**: regex the diff for digits + Author-year patterns; any hit is VERIFIED-in-ledger, verified now, or stripped. Micro-round drafts get the same style sweep.
- Trace every complaint to the procedure defect that produced it; fix the procedure, not just the text.
- Substance → another round. Wording-only → stop looping, apply directly.

## 5. Stop + ship
- Stop when: wording-only feedback, or a round adds <1 surviving major finding, or arbitrator (Fable-max or the owner) calls it.
- Ship: humanizer + filler pass → `pandoc --reference-doc=<your reference.docx>` → verify the docx before reporting ready.
- Close-out: actuals + lessons → DESIGN.md calibration log (**a lesson with an owner ruling becomes a rule at the next patch; without one it stays a candidate**); ledger close row; PREFLIGHT close checklist complete — the run is not closed until every line is checked.


## Agent notes (Claude Code and Codex read this file; follow your own line)

- Claude Code: as written above (reviewers and verifiers as Agent-tool workers, `model:'opus'`; Fable only as judge or arbitrator).
- Codex: reviewers and verifiers are `spawn_agent` sub-agents, or run sequentially in one session when multi-agent is off; use the strongest available Codex model for judge/arbitrator roles and a capable lower-cost model for routine lanes. Claude model labels do not transfer across hosts. Name the agent, actual model, context, and reasoning setting in each round file so rounds from both agents compare.
- Both: the register citations, the claim verification step and the owner's per-round feedback are unchanged.
