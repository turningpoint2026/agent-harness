---
name: verify-claims
description: Adversarially verify every factual claim in a report against its sources with read-only subagents; emits a claims ledger. Run before anything ships.
---

# Verify claims

The semantic half of the verification pair. `claims_ledger_gate.py` (PreToolUse hook) already guarantees that every claim in a deliverable **names** a source. This skill checks whether the named source **says the thing**.

Those are different failures and only the second one produced the 2026 incidents: a fabricated byline, a payment read as received off a ledger that did not say so, a rate comparison that overstated what the data showed. Each carried a plausible-looking citation.

## When this is mandatory

- Before a report, memo, PDF, or email attachment goes to a collaborator, editor, or publisher.
- Whenever a document contains claims produced by a subagent or a prior session (memory: `subagent-claims-are-not-verified`).
- Whenever `claims_ledger_gate.py` was bypassed via "override claims gate".

## Procedure

### 1. Establish the target and the corpus

Ask, or infer and state:
- **Target**: the exact file path of the document to verify.
- **Corpus**: the directories or files the claims should resolve against (data folders, source notes, PDFs, transcripts, email exports).

Print both back before doing anything. Wrong-target work was the second-largest friction class in the 2026-08-09 insights review; naming the path out loud is the cheap fix.

### 2. Extract claims

Read the target **in full** (no `limit`/`offset`). Enumerate every factual assertion into a numbered list. A claim is anything a reader could look up and find false:

| Class | Examples | Priority |
|---|---|---|
| Attribution | authorship, bylines, "X said/argued/found", coding-method and data-role claims | **highest** — hallucinates most |
| Status | was sent, has been paid, is confirmed, was approved, filed on | **highest** |
| Quantity | counts, currency, rates, sample sizes, effect sizes, dates | high |
| Causal / comparative | "A is larger than B", "X caused Y", "the gap persists" | high |
| Background | historical or institutional facts about firms, people, laws | medium |

Do not fold several assertions into one row. One row per checkable fact.

### 3. Fan out to adversarial verifiers

Spawn read-only subagents (Explore, or Agent with no write access), batched roughly 8-12 claims each so no agent holds the whole document. Give each agent this brief verbatim:

> You are an adversarial fact-checker. Your job is to **refute** each claim below, not to confirm it. Default to UNSUPPORTED when the evidence is merely consistent with the claim rather than stating it.
>
> For each claim: search the corpus at <paths>, open the actual file, and quote the exact supporting or contradicting sentence with `file:line`. Paraphrase is not evidence. A number that appears somewhere in a spreadsheet is not evidence that the sentence's interpretation of it is right.
>
> Return one row per claim: `id | verdict | file:line | verbatim quote | one-line reasoning`.
> Verdicts: **VERIFIED** (source states it), **UNSUPPORTED** (source does not state it, or says less than the claim), **CONTRADICTED** (source says otherwise), **UNVERIFIABLE** (no source in the corpus covers it).
> Edit nothing. Return data, not prose.

Rules:
- The verifier must **not** be told what the document concludes. Send the claim, not the argument.
- Attribution and status claims get a **second, independent** verifier. Two agents, same claim, no shared context. Disagreement means UNSUPPORTED, not a tiebreak.
- If the corpus contains no file that could settle a claim, the verdict is UNVERIFIABLE. That is a real result, not a failure.

### 4. Emit the ledger

Write `<target_stem>_claims_ledger.md` next to the target (the `_ledger` suffix is skipped by the claims gate by design):

```markdown
# Claims ledger — <target filename>
Verified <date> against <corpus paths>. <N> claims: <v> verified, <u> unsupported, <c> contradicted, <x> unverifiable.

| # | Claim | Class | Verdict | Evidence (file:line) | Quote |
|---|-------|-------|---------|----------------------|-------|
```

Then report to the user, in this order:
1. **CONTRADICTED** rows first, in full. These are live errors in the document.
2. **UNSUPPORTED** rows.
3. Counts only for VERIFIED and UNVERIFIABLE.

### 5. Do not auto-fix

Propose the repairs; do not apply them. Every non-VERIFIED row needs one of: cut the claim, hedge it in the prose, or relabel it `[UNVERIFIED: reason]`. Which one is the user's call, since it changes what the document argues. Direct edits need a fresh, turn-scoped greenlight.

## Honest limits

State these in the ledger rather than letting silence imply coverage:
- Verifiers can be wrong too. A VERIFIED row means an independent agent found and quoted supporting text, not that the claim is true.
- Tables and numeric-dense rows are outside the hook's gate, so the ledger is the **only** check they get. Include them.
- Coverage is bounded by the corpus you pointed at. Name what was excluded.


## Agent notes (Claude Code and Codex read this file; follow your own line)

- Claude Code: as written above (read-only verifier subagents).
- Codex: verifiers are `spawn_agent` sub-agents instructed read-only, or sequential passes in one session; the claims-ledger format and the adversarial stance are unchanged. A verifier's `read in full` is one whole-file `Get-Content`.
