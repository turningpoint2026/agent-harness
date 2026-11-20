# Substantive Audit Worker — Instructions

**For**: Fresh {{audit_agent}} session ({{audit_model_and_context}})
**Date issued**: {{date}}
**Output file**: `{{substantive_audit_report_absolute_path}}`
**Runtime target**: 45–90 minutes (it re-does real work on a sample, so it is slower than the mechanical audit)
**Scope**: SUBSTANTIVE correctness audit by independent re-derivation. Refute-first. Distinct from the mechanical audit. You MAY and MUST open raw source material.

---

## 0. How to use this doc

You are a fresh {{audit_agent}} session launched AFTER all content workers return (you may run alongside, or after, the mechanical-audit worker). You are NOT the orchestrator and you did not plan this work. That independence is the entire point: the orchestrator is invested in the plan and tends to bless its own outputs, so substantive correctness is checked HERE instead of being left to the party that designed the task.

Your job: take a sample of each worker's findings, independently re-derive each one from the raw source, and report where the worker's output fails to reproduce. Read this whole doc first.

**Pre-flight**: confirm every worker output listed in §3 exists. If any is missing, STOP and report that the audit cannot run on a partial snapshot (same rule as the mechanical audit). Also Glob/ls your own output path first; if a prior substantive-audit report exists, read it and extend rather than silently regenerate.

## 1. Project context (one paragraph)

{{project_context_paragraph}}

This paragraph must carry everything a fresh session needs. The orchestrator's conversation is NOT available to you. If a term is unfamiliar, flag it rather than guess.

## 2. The discipline: derive first, then compare (anti-anchoring)

The failure this audit exists to catch is a worker confidently producing a wrong or artifact-driven finding that reads as plausible. The risk for YOU is anchoring: if you read the worker's answer first, you will rationalize it. So for every sampled item:

1. Read ONLY the raw source for that item (the CSV row, the converted PDF passage, the dataset slice the worker used).
2. Write your OWN independent finding first, before looking at what the worker wrote.
3. THEN reveal the worker's finding and compare.

**Refute-first stance**: actively look for the strongest reason the worker is wrong, overclaiming, scope-stretching, or driven by an artifact (selection effect, brand or composition skew, partial PDF ingestion, sampling bias, a single source over-cited). When you cannot independently reproduce the worker's finding, the default verdict is DIVERGENT, never "probably fine."

## 3. Worker outputs to audit + their raw sources

For each worker: read its instruction MD (for the spec and the claim it was asked to produce), its output file (the findings), AND the raw source it drew from (so you can re-derive). Unlike the mechanical audit, you MUST open source material.

| Worker | Instruction MD | Output to audit | Raw source(s) to re-derive from |
|---|---|---|---|
| {{worker_1_name}} | `{{worker_1_instructions_path}}` | `{{worker_1_output_path}}` | `{{worker_1_source_paths}}` |
| {{worker_2_name}} | `{{worker_2_instructions_path}}` | `{{worker_2_output_path}}` | `{{worker_2_source_paths}}` |
| {{worker_N_name}} | `{{worker_N_instructions_path}}` | `{{worker_N_output_path}}` | `{{worker_N_source_paths}}` |

If a worker produced no falsifiable findings (pure synthesis with nothing to re-derive against a source), mark it **N/A for substantive audit** and say why. Do not invent things to check.

## 4. Procedure — run against EACH worker

1. **Enumerate the worker's falsifiable findings.** These are claims that could be WRONG, not merely missing: labels/codes assigned, facts or quotes harvested, classifications, extracted values, computed figures. (Structural completeness, null-flagging, and citation traceability are the mechanical audit's job, not yours.)
2. **Sample.** Default {{sample_rate}} (10%), minimum 5, cap 20 items per worker. If the worker has fewer than 5 findings, take all. Spread the sample across sections/sources, do not draw it all from one place. Record exactly how you sampled.
3. **Re-derive each sampled item** per the §2 discipline (raw source first, your own answer first, then compare).
4. **Assign a verdict per item, with forced evidence**:
   - **REPRODUCED** — your independent finding matches the worker's. Quote the raw passage that supports it.
   - **DIVERGENT** — your finding differs (wrong label, overclaim, scope stretch, missed artifact, miscount). Quote the raw passage AND state both findings (worker's vs. yours).
   - **UNVERIFIABLE** — you cannot reach the raw source (paywalled, OCR-only, file missing, conversion failed). State why. Do NOT guess a verdict.
5. **Estimate the worker's error signal**: divergent / sampled (e.g., 3/15). Describe the KINDS of divergence, not just the count.
6. **Check for systematic artifacts, not just per-item errors.** Does the divergence cluster on one brand, one source type, one date range, one section? A clustered error is more serious than scattered noise: it suggests the whole output is skewed, not merely imperfect, and points to RE-RUN rather than SPOT-FIX.

## 5. Output spec — produce this file

Create `{{substantive_audit_report_absolute_path}}`. Use this structure:

```markdown
# Substantive Audit Report

**Date**: {{date}}
**Audited by**: substantive_audit worker ({{audit_agent}}; {{audit_model_and_context}}) — independent of the orchestrator
**Method**: independent re-derivation of a sampled subset; refute-first; forced-evidence verdicts

## Reproduction summary

| Worker | Findings | Sampled | Reproduced | Divergent | Unverifiable | Reproduction rate | Recommendation |
|---|---|---|---|---|---|---|---|
| {{worker_1_name}} | | | | | | | ACCEPT / SPOT-FIX / RE-RUN |
| ... | | | | | | | |

Recommendation heuristic (advisory; the orchestrator decides):
- 0 divergent in sample → ACCEPT
- 1–2 scattered divergent → SPOT-FIX the named items
- divergence clusters on one dimension, or exceeds ~20% of the sample → RE-RUN the worker (likely systematic, not noise)

## Per-worker detail

### {{worker_1_name}} — `{{worker_1_output_path}}`

- **Sampling**: [N of M findings; how chosen; what spread]
- **Verdicts**:
  - [item id] — REPRODUCED — raw quote: "..."
  - [item id] — DIVERGENT — worker said X; I derive Y from raw quote: "..."
  - [item id] — UNVERIFIABLE — [why the source was unreachable]
- **Artifact check**: [clustered? on what dimension? or "no clustering detected"]
- **Recommendation**: [ACCEPT / SPOT-FIX / RE-RUN] + one-line why

### {{worker_N_name}} — ...

## What this report does NOT do

Mechanical structure, existence, null-flagging, and citation-traceability checks are the mechanical-audit worker's job. This report judges substantive correctness on a SAMPLE only. A clean sample does not prove the whole output is correct; it bounds the error rate. State that bound, do not overclaim it as full coverage.
```

## 6. Hard rules

- **You are independent.** You did not plan this work. Do not defer to "the worker probably had context." Re-derive from raw sources.
- **Derive before peeking (§2).** If you catch yourself reading the worker's answer before forming your own, that item is contaminated: discard it and re-sample a replacement.
- **Forced evidence.** No verdict without a raw quote (REPRODUCED, DIVERGENT) or a stated reason (UNVERIFIABLE). A verdict with no evidence is a bluff. Delete it.
- **Default to DIVERGENT under uncertainty, never to ACCEPT.** A false ACCEPT reaches downstream readers; a false DIVERGENT only costs the orchestrator a second look.
- **PDF/DOCX**: convert before reading (`pdftotext -layout input.pdf output.txt` or `pandoc input.pdf -o output.md`; `pandoc input.docx -o output.md`). A null re-derivation from a partially-ingested PDF is UNVERIFIABLE, not REPRODUCED. Sanity-check byte counts after conversion.
- **Sample honestly.** Report exactly how you sampled. Do not cherry-pick easy items to inflate the reproduction rate.
- **Stay in your lane.** Do not fix the worker's output, do not synthesize across workers, do not re-plan the decomposition. Report; the orchestrator acts.

## 7. What to do when done

Report back:

```
Substantive audit report: {{substantive_audit_report_absolute_path}}

Reproduction: [per worker, e.g. W1 13/15, W2 9/12]
RE-RUN recommended for: [workers, or "none"]
Systematic artifacts flagged: [list with the dimension they cluster on, or "none detected"]

Time spent: [actual runtime]
```

Do NOT continue into fixing, synthesis, or adjacent tasks. The orchestrator reads this report and decides next steps.
