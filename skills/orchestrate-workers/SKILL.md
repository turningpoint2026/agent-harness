---
name: orchestrate-workers
description: "Set up parallel worker sessions for 2+ independent, testable subtasks: master plan, worker instruction MDs, launch prompts, INDEX, scope-creep log."
---

# /orchestrate-workers

**Mistake registers (binding):** before generating worker instruction MDs, read `<HARNESS_ROOT>/registers/_REGISTERS_INDEX.md` (private in the original harness) and route to the register(s) matching the task type; every worker instruction MD cites the relevant register as a binding checklist; the orchestrator's audit checks compliance and appends new lessons at close-out. Unsure which register fits: ask the owner first.

## What this skill does

Generates the file scaffolding for the **orchestrator-workers** pattern: one orchestrator session (the current session) plans the work and audits outputs; N worker sessions (opened manually by the user in separate Claude Code or Codex windows) each read one self-contained instruction MD and produce one discrete output file. Workers never talk to each other — all coordination happens through files on disk.

The skill produces five artifact types per run:

1. A **master plan doc** — `[task_name]_plan.md`
2. **N worker instruction MDs** — `[worker_name]_instructions.md`
3. **N launch prompts** — one clean pointer sentence per worker, pasted into fresh sessions
4. An **INDEX.md** — status tracker for the working directory
5. A **scope-creep log** — empty running log for deferred ideas

Validated on 2026-04-19 on a three-worker run for a research article draft (a case harvest, a targeted case search, a skeleton builder).

## When to use

**Parallelism alone no longer picks this skill** (rule settled with the owner 2026-08-24, after `/loop-improve` v1.0): in-session Workflow fan-out handles parallel agents fine, and separate windows draw on the SAME token pool (memory: measure-binding-constraint-before-parallelising). Use this skill when the work is **independent multi-artifact production** — several separate outputs with separate audiences/audits, converging on no shared draft — AND at least one of:

- Output artifacts that **benefit from human audit** between phases
- You want each worker's **full transcript**, not a summary string
- **Crash resilience / multi-day runs**: worker sessions survive reboots and span usage windows; Workflow cache dies with its session
- Combined context large enough to degrade a single session. **On Opus 4.8 / Fable with the 1M window this is a weak trigger on its own** — do not split on token volume alone.

**Division of labor with `/loop-improve`**: one document the owner reads and approves, improved over feedback rounds with claim verification → `/loop-improve` (its internal fan-out is not a reason to come here). Several independent artifacts → this skill. Composed use = "RAs first, editor second": these workers produce + audit the analysis artifacts, then `/loop-improve` writes the reader-facing document from their audited reports (its read-only sources). Sequence, never nest.

## When NOT to use

- **Quick one-shot delegations** — use an in-session sub-agent instead
- **Sequential workflows with tight feedback loops** — parallel windows add overhead
- Tasks where the user **doesn't want to manage multiple windows**
- **Single-worker tasks** — no parallelism to orchestrate

## Workflow

### Step 1 — Clarify the task

Ask the orchestrator user (in this order, do not skip):

1. **Overall goal**: what is the desired end-state?
2. **Input material**: exact file paths, folder paths, URLs the workers should read
3. **Expected final deliverable**: what will the orchestrator produce after workers return?
4. **Deadline** (if any)
5. **Working directory**: absolute path where all artifacts will live. If not specified, default to the current project root.

If the user invoked `/orchestrate-workers` with no argument, ask for the task description first.

### Step 2 — Propose a decomposition

Draft a worker breakdown and present it to the user. Wait for explicit approval before generating any files.

For each worker, specify:
- **Worker name** (e.g., `A1_case_harvest`, `B_skeleton_builder`)
- **Agent and model**: Claude Code or Codex, plus the actual model/context setting for that lane
- **Scope**: one task per worker, narrow and testable
- **Independence**: confirm the worker's output does not depend on any other worker's output. If a worker depends on another's output, flag it — the skill's sweet spot is fully parallel. Sequential flows should use the `Agent` tool instead.
- **Output artifact**: exact file path
- **Runtime estimate**

Typical worker count: **2–5**. More needs justification — too many workers strains the user's window-management bandwidth.

**Also ask**: "Want an optional **mechanical-audit worker** that runs after the others? It reads each worker's output file and verifies file exists, output structure matches spec, citations are traceable, and null results are flagged. Pure mechanical checks; substantive audit stays with you as orchestrator unless you also add the substantive-audit worker below. (y/n)"

**Also ask (second, separate opt-in)**: "Want an optional **adversarial substantive-audit worker**? Distinct from the mechanical audit: it independently re-derives a ~10% sample of each worker's findings from the raw sources, takes a refute-first stance, and quotes raw evidence for every verdict, so substantive *correctness* isn't left to you, the orchestrator who planned the work and is inclined to bless its outputs. It launches after the content workers (may run alongside the mechanical audit) and, unlike the mechanical audit, it DOES open source material. Worth it when worker outputs feed collaborators or downstream analysis and a wrong finding is costly; skip it for low-stakes or exploratory runs. (y/n)"

Present the proposal like this:

```
Proposed decomposition for: [task]
Working directory: [absolute path]

Worker A1 — [name]
  Agent/model: [Claude Code or Codex] / [actual model and context]
  Scope: [one sentence]
  Input: [paths]
  Output: [absolute path]
  Runtime: [estimate]

Worker A2 — [name]
  ...

Optional final worker (if user opted in):
Worker Z — mechanical_audit
  Scope: verify each worker's output passes mechanical checks (file exists, structure matches spec, citations traceable, null results flagged)
  Input: all worker output paths above
  Output: [working_dir]/mechanical_audit_report.md
  Runtime: 15–30 min
  Launch AFTER all other workers return.

Optional substantive-audit worker (if user opted in):
Worker Y — substantive_audit
  Scope: independently re-derive a ~10% sample of each worker's findings from the raw sources; refute-first; forced-evidence verdicts (REPRODUCED / DIVERGENT / UNVERIFIABLE)
  Input: each worker's output path AND the raw sources that worker used
  Output: [working_dir]/substantive_audit_report.md
  Runtime: 45–90 min
  Independence: MUST be a separate session from the orchestrator. Launch AFTER all content workers return; may run alongside Worker Z.

Does this look right? Reply "approved" to generate files, or edit the scope.
```

### Step 3 — Generate artifacts

After the user approves, generate the five artifact types by copying the templates from `templates/` and substituting `{{placeholders}}`:

1. **Master plan** → `[task_slug]_plan.md` from `templates/master_plan.md`
2. **Worker instruction MDs** → one per worker, from `templates/worker_instructions.md`
3. **Launch prompts** → print to screen as copy-paste blocks, using `templates/launch_prompt.txt`
4. **INDEX.md** → from `templates/index.md`
5. **Scope-creep log** → `scope_creep_log.md` from `templates/scope_creep_log.md` (starts empty)

**If the user opted into the mechanical-audit worker in Step 2**, also generate:

6. **Audit worker instructions** → `mechanical_audit_instructions.md` from `templates/mechanical_audit.md`, with every other worker's output path pre-substituted into the inputs section
7. **Audit launch prompt** — generated from `templates/launch_prompt.txt` but labeled "LAUNCH LAST — only after all other workers return" and listed at the bottom of the hand-off output

**If the user opted into the substantive-audit worker in Step 2**, also generate:

8. **Substantive-audit worker instructions** → `substantive_audit_instructions.md` from `templates/substantive_audit.md`, with every content worker's output path AND its raw-source paths pre-substituted into §3.
9. **Substantive-audit launch prompt** — from `templates/launch_prompt.txt`, labeled "LAUNCH AFTER content workers; independent session; may run alongside the mechanical audit" and listed at the bottom of the hand-off output.

Fill every template's agent/model placeholders from the approved roster. Do not translate a Claude model name into a Codex model name or claim equal context windows; record the actual host, model, context, and reasoning setting used.

**Detect existing files.** If the working directory already has a plan doc, INDEX, or scope log, ASK the user whether to integrate or overwrite. Default is integrate (edit existing) rather than duplicate.

#### Keep the launch prompt to a one-line pointer

The launch prompt is NOT where scope guards live. The worker instruction MD is self-contained by hard rule — §0 tells the worker to read the whole doc, §3 lists its inputs, §7 lists what's out of scope (the other workers' jobs). Duplicating any of that into the prompt creates two sources of truth that drift apart. So the prompt carries only the two things a fresh window cannot infer:

1. **Which file to read** — the absolute path to the worker's instruction MD
2. **Read it in full before acting** — cheap insurance against skimming

That is the whole prompt:

```
You are worker [W#] in a parallel file-based orchestration. Read this file IN FULL, then execute it exactly as written:
[absolute path to the worker's instruction MD]
```

Two things the prompt deliberately does NOT carry:

- **The model.** A pasted prompt cannot change the session model. The approved roster and instruction-doc header record each lane's actual agent/model. Print that roster reminder ONCE above all prompt blocks, not inside each one.
- **Scope guards.** They live in the doc's §7. If you feel the urge to paste scope guards into the prompt, the real problem is that the doc isn't self-contained — fix the doc, not the prompt.

### Step 4 — Hand off

Print the N launch prompts on screen as one-line pointer blocks (see "Keep the launch prompt to a one-line pointer" above). Print the model reminder ONCE at the top, not inside each block. Then tell the orchestrator session to wait — do NOT proceed to audit until the user pastes back the worker output paths.

Output looks like:

```
Scaffolding generated. Next steps:

1. Open each worker in the agent/model named in the approved roster and INDEX. Claude Code and Codex lanes may be mixed.
2. Paste the one-line launch prompt for each worker (below) into its window. Launch in WAVES if there are dependencies — open a later wave only after the prior wave's outputs are on disk.
3. When each worker reports back with an output file path, return to this session and paste the path. I will audit each output as it arrives.

─── Launch prompt for Worker A1 ───
[one-line pointer block]

─── Launch prompt for Worker A2 ───
[one-line pointer block]

...
```

## Templates

Located in `templates/` adjacent to this file. All use `{{placeholder}}` syntax for substitution.

- `master_plan.md` — master plan doc
- `worker_instructions.md` — one worker's self-contained instruction MD (seven sections, 0–7)
- `launch_prompt.txt` — one-line pointer launch block (path + "read in full"; scope guards stay in the doc)
- `index.md` — working-directory index
- `scope_creep_log.md` — empty running log
- `mechanical_audit.md` — **optional** blocked-last audit worker that runs mechanical checks against every other worker's output (file exists, structure matches spec, citations traceable, null results flagged). Substantive audit stays with the orchestrator.
- `substantive_audit.md` — **optional** adversarial correctness auditor that runs AFTER the content workers. Independently re-derives a sampled subset of each worker's findings from the raw sources (refute-first; forced-evidence: REPRODUCED / DIVERGENT / UNVERIFIABLE), then recommends ACCEPT / SPOT-FIX / RE-RUN per worker. Reuses the hostile-reviewer stance and the citation-verification re-derivation pattern. Unlike `mechanical_audit.md`, it opens source material: the mechanical audit checks structure, this checks correctness.

## Hard rules

- **Worker instruction MDs MUST be self-contained.** A fresh session that never saw the orchestrator's conversation must be able to read one MD and work from it without further context. Repeat context inside each MD — no cross-references to things only the orchestrator knows.
- **Launch prompts are a one-line pointer, not a scope briefing.** Each prompt carries only the absolute path to the worker's instruction MD plus "read it in full before acting." Scope guards live in the doc's §7; the model is set by the user when opening the window; the model reminder is printed once above all blocks. The user pastes the prompt as-is into a fresh window with zero editing. See Step 3 subsection "Keep the launch prompt to a one-line pointer." This works only because worker instruction MDs are self-contained (next rule) — if a prompt seems to need scope guards, fix the doc.
- **Every worker produces a discrete output FILE**, not a message-back summary. The orchestrator audits the artifact.
- **Orchestrator waits for worker outputs before auditing.** Do not proceed on assumption. Do not fabricate what a worker "would say." Real workers, real artifacts.
- **Model is explicit per worker.** Claude Code lanes use Opus 4.8 with [1m] context and xhigh reasoning. Codex lanes use the strongest suitable pinned Codex model and the highest supported reasoning setting for the role. Record the actual model/context in the plan, INDEX, instruction MD, and output; never present different vendors' model names as equivalent.
- **Never silently automate a manual-window plan.** The user opens the windows manually unless the approved decomposition explicitly assigns Codex `spawn_agent` lanes. Invoking this skill alone does not authorize automated delegation.
- **Never poll for worker outputs.** The orchestrator resumes when the user pastes paths back in.

## Common pitfalls to encode into worker instructions

### Source-search workers: PDFs and DOCX silently drop content

When a worker's job involves **searching PDF or DOCX files** for quotes, facts, or content — harvest workers, fact-check workers, lit-review workers, endnote-audit workers — reading those files directly via the Read tool produces unreliable results. PDFs are paginated with a per-request page cap (20 pages in current Claude Code), and reads silently stop at the cap without signaling that later pages went uningested. DOCX rendering loses structured content (tables, footnotes, tracked changes, comments) and may return only partial body text. A worker that grepped an 80-page PDF and reported "not found" may have only ingested the first 20 pages. Null results from partial ingestion are search failures disguised as absence of evidence, and they corrupt the orchestrator's audit.

**Rule to encode in any search/harvest worker's instruction MD** (§3 Inputs and §5 Hard rules):

> Before grepping or reading any PDF or DOCX, convert it to Markdown or plain text first:
> - **PDFs**: `pdftotext -layout input.pdf output.txt` or `pandoc input.pdf -o output.md`
> - **DOCX**: `pandoc input.docx -o output.md` (pandoc is installed on standard setups)
> - **Scanned/image-only PDFs**: flag explicitly as "OCR required; search gap" — do not report a null result
> - After conversion, **sanity-check byte counts** (a 50-page PDF producing a 2KB .txt = conversion failure)
> - **Document every conversion** in methodology notes (which file → which tool → which output path), so the orchestrator can trust null results

This rule is non-optional for search/harvest workers. For workers that do not search source documents (writing workers, synthesis workers, decision-memo workers), omit — the rule is pointless overhead.

### Output file may already exist from a prior run

When re-launching a worker (for retry, recovery from a failed WZ audit, or resumption after a closed window), the specified output file may already be on disk. Workers that silently regenerate it waste runtime and can overwrite verified work. This happened on 2026-04-20: a re-launched worker session spent ~70 min on parallel research before discovering the output file was already present from an earlier W5 run.

**Rule (already baked into the worker instructions template §0)**: every worker Globs or ls's the output path BEFORE starting research. If the file exists with substantive content, the worker reads it fully and reports "output already on disk — here's what's present / what's missing" rather than regenerating. The orchestrator also populates the **Output on disk?** column in `INDEX.md` at launch time (`absent` pre-launch; `present — complete` if a prior run's artifact is there) so the worker sees status at a glance.

**Orchestrator-side discipline**: when re-launching a worker (after a crash, window close, or missing WZ artifact), first check the working directory and INDEX.md. If a prior output exists, either (a) explicitly tell the new worker to *extend/update* the existing file rather than regenerate, or (b) move the prior file to an `_archive/` subfolder before re-launching so the new worker starts clean. Do not assume a re-launched worker will detect the duplication on its own.

### Other pitfalls worth encoding by worker type

- **Web-research workers**: "local files first, web second" per the global rule file — but explicitly list the local folders to search first so the worker does not jump to web out of habit.
- **Writing workers engaging academic sources**: every source claim must trace to a specific passage with page number in a canonical reference document (NotebookLM output, prior source audit, etc.). Paraphrase from memory is forbidden.
- **Research workers with time budgets**: if one target consumes the full runtime, the others will be empty. Set a per-target cap (e.g., "30 min per target; flag NULL and move on if exceeded") and make null results a first-class deliverable, not a failure state.

## Reference implementation

The validated 2026-04-19 run used three parallel workers; each produced a discrete output file the orchestrator audited before synthesis. (The private example file is not included.)


## Agent notes (Claude Code and Codex read this file; follow your own line)

- Claude Code: as written above (workers are separate Claude sessions launched from the instruction MDs, or the Agent tool).
- Codex: workers are separate Codex sessions (`codex exec` for unattended lanes, a TUI window for supervised ones) or `spawn_agent` sub-agents; the master plan, the per-worker instruction MDs, INDEX and the scope-creep log are the same files, and every worker handoff carries `AGENT: codex <session id> <HH:MM>`. Workers of either agent can be mixed; the INDEX names the agent and actual model per lane.
- Both: the mistake-register citation rule in every worker prompt is unchanged.
