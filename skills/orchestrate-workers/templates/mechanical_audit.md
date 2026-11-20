# Mechanical Audit Worker — Instructions

**For**: Fresh {{audit_agent}} session ({{audit_model_and_context}})
**Date issued**: {{date}}
**Output file**: `{{audit_report_absolute_path}}`
**Runtime target**: 15–30 minutes
**Scope**: MECHANICAL checks only on the outputs of other workers. Verify structural integrity. Do NOT do substantive audit. Do NOT synthesize across outputs. Do NOT rewrite or polish.

---

## 0. How to use this doc

You are a fresh {{audit_agent}} session launched AFTER all other workers have returned. Your ONLY job is mechanical verification of the output files they produced. You read each worker's output file, run a fixed checklist against it, and produce a single audit report. The orchestrator handles substantive audit — you do not.

**CRITICAL**: this doc must be launched LAST. If any of the worker output files listed in §3 does not yet exist, STOP and report back that the audit cannot run yet. Do not wait, do not retry, do not start checking whichever outputs are available — the orchestrator needs a consistent snapshot.

## 1. Project context (one paragraph)

{{project_context_paragraph}}

## 2. What you do NOT do

- Do NOT evaluate whether worker outputs are "good" or "advance the project's goals" — that is substantive audit, and it is the orchestrator's job. You lack the conversation context needed for substantive judgment.
- Do NOT synthesize findings across worker outputs.
- Do NOT propose changes, rewrites, or follow-up tasks.
- Do NOT edit the worker output files.
- Do NOT open source material (PDFs, CSVs, etc.) — your inputs are the worker output files and their declared output specs, nothing else.

## 3. Worker outputs to audit

For each worker output listed below, also read the worker's own instruction MD to know what the output SPEC required. Audit each output against its own spec.

| Worker | Instruction MD | Output to audit |
|---|---|---|
| {{worker_1_name}} | `{{worker_1_instructions_path}}` | `{{worker_1_output_path}}` |
| {{worker_2_name}} | `{{worker_2_instructions_path}}` | `{{worker_2_output_path}}` |
| {{worker_N_name}} | `{{worker_N_instructions_path}}` | `{{worker_N_output_path}}` |

## 4. Mechanical checklist — run against EACH worker output

For each worker's output file, check:

1. **Existence**: file exists at the specified path. If missing, STOP and report (per §0).
2. **Structural match**: the output contains the top-level sections the worker's instruction MD §4 (Output spec) required. Missing sections flagged explicitly.
3. **Null-results honesty**: if the worker's task included searches or targets, a "Null results" section exists and names specific targets that came up empty. Silent omission is a FAIL.
4. **Citation traceability**: every factual claim that quotes, paraphrases, or references an external source has a file path or URL attached. Flag any quote or claim that lacks a traceable source.
5. **No fabrication markers**: scan for `[UNVERIFIED]`, `[NEEDS SOURCE]`, `[GAP]`, `[AWAITING]`, or similar honesty flags. Count them and list them — these are the orchestrator's follow-up queue.
6. **Scope adherence**: the output does NOT contain synthesis, prose drafting, or tasks that belonged to other workers (scan the "What NOT to do" list in the worker's §7).
7. **Time/methodology transparency**: the output has a "Methodology notes" or equivalent section stating what was searched, what terms were used, what came up empty.

## 5. Output spec — produce this file

Create `{{audit_report_absolute_path}}`. Use this structure:

```markdown
# Mechanical Audit Report

**Date**: {{date}}
**Audited by**: mechanical_audit worker ({{audit_agent}}; {{audit_model_and_context}})
**Workers audited**: [list worker names]

## Summary table

| Worker | File exists | Structure matches spec | Nulls flagged | Citations traceable | Scope adhered | Overall |
|---|---|---|---|---|---|---|
| {{worker_1_name}} | PASS / FAIL | PASS / FAIL | PASS / FAIL / N/A | PASS / FAIL / N/A | PASS / FAIL | PASS / FAIL |
| ... | | | | | | |

## Per-worker findings

### {{worker_1_name}} — `{{worker_1_output_path}}`

- **Existence**: [PASS/FAIL + note]
- **Structural match**: [PASS/FAIL; list missing sections if any]
- **Null-results honesty**: [PASS/FAIL/N-A; list targets with no stated null result]
- **Citation traceability**: [PASS/FAIL; list N untraceable claims by line reference]
- **Fabrication markers found**: [count + list with line references, e.g., "3 × [UNVERIFIED]"]
- **Scope adherence**: [PASS/FAIL; list any out-of-scope content]
- **Methodology transparency**: [PASS/FAIL]
- **Flags for orchestrator**: [any unusual patterns — suspiciously complete quotes, same source cited many times, very short output, etc.]

### {{worker_N_name}} — ...

## Orchestrator follow-up queue

Consolidated list of every `[UNVERIFIED]`, `[NEEDS SOURCE]`, `[GAP]`, `[AWAITING]` marker found across all outputs, with worker + file path + line number. This is the orchestrator's to-do list for substantive audit.

## What this report does NOT do

This audit is mechanical only. It does not assess whether the outputs advance the project's goals, whether the findings are theoretically sound, or whether worker synthesis is warranted. Those judgments remain with the orchestrator.
```

## 6. Hard rules

- **Mechanical only.** If you find yourself forming an opinion on whether a finding is interesting or important, STOP — that is substantive audit.
- **Read each worker's instruction MD** before auditing its output. The mechanical check is against the worker's OWN spec, not a universal ideal.
- **No bluffing.** If you cannot determine whether a citation is traceable without reading external files, flag it as "needs orchestrator verification" rather than declaring PASS or FAIL.
- **Preserve the paper trail.** Every FAIL finding has a specific line reference in the worker output so the orchestrator can jump to it.

## 7. What to do when done

Report back:

```
Audit report: {{audit_report_absolute_path}}

Summary: [N workers passed mechanical audit, M failed; K unverified markers aggregated into orchestrator follow-up queue]

Flags for orchestrator attention:
- [any patterns that warrant a second look — not substantive judgment, just "worth looking twice"]

Time spent: [actual runtime]
```
