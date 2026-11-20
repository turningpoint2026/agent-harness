# {{task_name}} — Master Plan

**Date**: {{date}}
**Deadline**: {{deadline_or_none}}
**Working directory**: {{working_dir_absolute_path}}
**Orchestrator session**: this file's author
**Pattern**: orchestrator-workers (parallel windows, file-based handoff)

---

## 1. Task summary

{{one_paragraph_task_summary}}

**Final deliverable**: {{final_deliverable_description}}

## 2. Worker roster

| Worker | Agent/model | Scope (one sentence) | Expected output artifact | Runtime est. |
|---|---|---|---|---|
| {{worker_1_name}} | {{worker_1_agent_model}} | {{worker_1_scope}} | `{{worker_1_output_path}}` | {{worker_1_runtime}} |
| {{worker_2_name}} | {{worker_2_agent_model}} | {{worker_2_scope}} | `{{worker_2_output_path}}` | {{worker_2_runtime}} |
| {{worker_N_name}} | {{worker_N_agent_model}} | {{worker_N_scope}} | `{{worker_N_output_path}}` | {{worker_N_runtime}} |

All workers run **in parallel**. Workers do not read each other's outputs.

## 3. Artifact paths

**Instruction MDs** (one per worker, self-contained, fresh-session readable):
- `{{working_dir}}/{{worker_1_name}}_instructions.md`
- `{{working_dir}}/{{worker_2_name}}_instructions.md`
- `{{working_dir}}/{{worker_N_name}}_instructions.md`

**Worker outputs** (produced by workers):
- `{{worker_1_output_path}}`
- `{{worker_2_output_path}}`
- `{{worker_N_output_path}}`

**Orchestrator tracking**:
- `{{working_dir}}/INDEX.md` — folder index + status tracker
- `{{working_dir}}/scope_creep_log.md` — deferred ideas

## 4. Orchestrator audit checklist

When each worker returns, the orchestrator verifies:

### {{worker_1_name}}
- [ ] Output file exists at the specified path
- [ ] {{worker_1_audit_criterion_1}}
- [ ] {{worker_1_audit_criterion_2}}
- [ ] Null results are flagged explicitly (not silently omitted)
- [ ] All claims traceable to a file path or verified URL

### {{worker_2_name}}
- [ ] Output file exists at the specified path
- [ ] {{worker_2_audit_criterion_1}}
- [ ] {{worker_2_audit_criterion_2}}
- [ ] Null results flagged
- [ ] Claims source-traceable

### {{worker_N_name}}
- [ ] Output file exists at the specified path
- [ ] {{worker_N_audit_criterion_1}}
- [ ] Null results flagged
- [ ] Claims source-traceable

## 5. Post-worker synthesis plan

After all workers return and pass audit:

1. {{synthesis_step_1}}
2. {{synthesis_step_2}}
3. {{synthesis_step_3}}

**If a worker fails audit**: send targeted follow-up via the same window (do not re-launch from scratch). Log the re-work in INDEX.md.

## 6. Scope discipline

Mid-run ideas, reviewer-anticipation concerns, and theoretical extensions go into `scope_creep_log.md` — not into this plan or worker instructions. Revisit after synthesis.
