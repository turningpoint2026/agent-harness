# {{project_or_task_name}} — Folder Index

**Last updated**: {{date}}
**Deadline**: {{deadline_or_none}}
**Status**: {{status_line_e_g_workers_running_in_parallel_sessions}}

## Live working docs

| File | Role | Status |
|---|---|---|
| `{{plan_filename}}` | Master plan — task summary, worker roster, audit checklist, synthesis plan | **Active** — everyone reads |
| `INDEX.md` | This file — folder index + status tracker | Active |
| `scope_creep_log.md` | Running log of mid-run ideas to defer | Active (writable by all workers) |
| {{other_live_doc_row}} | {{other_live_doc_role}} | {{other_live_doc_status}} |

## Worker instruction docs

| Worker | Agent/model | Instructions | Expected output | Output on disk? | Runtime est. | Status |
|---|---|---|---|---|---|---|
| {{worker_1_name}} | {{worker_1_agent_model}} | `{{worker_1_instructions_filename}}` | `{{worker_1_output_filename}}` | {{worker_1_output_on_disk}} | {{worker_1_runtime}} | {{worker_1_status}} |
| {{worker_2_name}} | {{worker_2_agent_model}} | `{{worker_2_instructions_filename}}` | `{{worker_2_output_filename}}` | {{worker_2_output_on_disk}} | {{worker_2_runtime}} | {{worker_2_status}} |
| {{worker_N_name}} | {{worker_N_agent_model}} | `{{worker_N_instructions_filename}}` | `{{worker_N_output_filename}}` | {{worker_N_output_on_disk}} | {{worker_N_runtime}} | {{worker_N_status}} |

Status legend: `awaiting launch` → `running` → `returned — awaiting audit` → `passed audit` → `failed — see notes`

**Output-on-disk legend** (workers must check this column before starting): `absent` (normal pre-launch state) / `present — in progress` (current worker's draft) / `present — complete` (done; do NOT regenerate — read it and report gaps). A worker launched against a `present — complete` file should NOT overwrite; it should read the file and report what's already covered.

## Subfolders

{{subfolder_summary_or_none}}

## What to do when workers report back

1. Read each worker's output file in full — do not rely on the one-paragraph summary.
2. Run the audit checklist from `{{plan_filename}}` §4 against the output.
3. If the output passes, update this INDEX to `passed audit` and proceed to synthesis.
4. If the output fails, send a targeted follow-up in the same worker window (do not re-launch from scratch). Log the re-work here.
5. After all workers pass audit, execute the synthesis plan from `{{plan_filename}}` §5.
6. Mid-run ideas go to `scope_creep_log.md` — do not expand worker scope mid-stream.
