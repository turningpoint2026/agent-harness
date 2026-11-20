# {{worker_display_name}} — Instructions

**For**: Fresh {{worker_agent}} session ({{worker_model_and_context}})
**Date issued**: {{date}}
**Output file**: `{{worker_output_absolute_path}}`
**Runtime target**: {{runtime_estimate}}
**Scope**: {{one_line_scope_summary}}

---

## 0. How to use this doc

You are a fresh {{worker_agent}} session. Read this whole doc first. Then do the work per §2 and §3 (target + inputs). Then write the single output file specified in the frontmatter. When done, report back the output file path and a one-paragraph summary of what you found (including null results).

Do NOT do the work assigned to other workers (listed in §7). Do NOT write prose, synthesis, or polish beyond the output spec. Do NOT modify files other than the output.

**Pre-flight: check whether the output file already exists.** Before launching any research, Glob or ls the output path in §5 below. If the file already exists with substantive content, READ IT IN FULL first. A prior run may have produced it; the orchestrator may have re-launched you by mistake, or a parallel window may have completed the same scope. If the existing file already covers your scope: report back "output already on disk — reviewed, here's what's present and what's missing" rather than regenerating. If it partially covers your scope: extend/update rather than overwrite. Silent regeneration wastes the orchestrator's time and can overwrite verified work. Also check the working directory's `INDEX.md` for status markers on your output file before starting.

## 1. Project context (one paragraph)

{{project_context_paragraph}}

This paragraph must contain everything a fresh session needs to understand why this work matters — the orchestrator's conversation is NOT available to you. If a term or reference is unfamiliar, flag it rather than guess.

## 2. Target — what THIS worker produces

{{specific_target_description}}

Concrete deliverable: a markdown file at `{{worker_output_absolute_path}}` containing {{output_content_summary}}.

## 3. Inputs — read these files

Priority 1 — MUST READ:

1. `{{input_1_path}}` — {{input_1_purpose}}
2. `{{input_2_path}}` — {{input_2_purpose}}
3. `{{input_N_path}}` — {{input_N_purpose}}

Priority 2 — reference as needed:

- `{{optional_input_1_path}}` — {{optional_input_1_purpose}}
- `{{optional_input_2_path}}` — {{optional_input_2_purpose}}

**Search commands** (if the worker involves harvesting or searching):

- `Glob`: {{glob_patterns_if_any}}
- `Grep`: {{grep_patterns_if_any}}
- `Read`: for any individual file that looks promising

**Local files first, web second** — per project rule. Only use WebFetch/WebSearch if local sources are exhausted, and cite the URL explicitly.

## 4. Output spec

Create the file at `{{worker_output_absolute_path}}`. Use this structure:

```markdown
# {{output_doc_title}}

**Date**: {{date}}
**Produced by**: {{worker_display_name}} ({{worker_agent}}; {{worker_model_and_context}})
**Inputs searched**: [list actual folders/files you read]

## {{section_1_heading}}

{{section_1_content_guidance}}

## {{section_2_heading}}

{{section_2_content_guidance}}

## Null results

If specific targets returned nothing after thorough search, list them explicitly here. This is useful information — do not silently omit.

## Methodology notes

One short paragraph: what was searched, what search terms were used, what came up empty, what might deserve a second pass.
```

## 5. Hard rules

- **Scope discipline**: stay inside §2. No framing, no synthesis beyond what the output spec asks for, no theoretical extensions.
- **No fabrication**: every factual claim traceable to a file path or verified URL. If you cannot trace, flag as `[UNVERIFIED: reason]` — do not invent.
- **Citation style**: {{citation_style_instruction}}
- **Time budget**: {{time_budget}}. If a specific target takes more than {{per_target_time_cap}} with no result, move on and report null.
- **Honest null results**: if a target returned nothing, say so explicitly. Silent omission is worse than flagged absence.
- **PDF/DOCX conversion** (if the worker searches source documents): convert PDFs and DOCX files to Markdown or plain text BEFORE grepping or reading. PDFs are paginated with a per-request page cap and silently drop later pages; DOCX rendering loses structured content. Use `pdftotext -layout input.pdf output.txt` or `pandoc input.pdf -o output.md` for PDFs; `pandoc input.docx -o output.md` for DOCX. Sanity-check byte counts post-conversion. Document every conversion in methodology notes. A null result from a partially-ingested PDF is a search failure disguised as absence of evidence. (Omit this bullet if the worker does not search source documents.)
- **Encoding** (if applicable): {{encoding_notes_if_any}}

## 6. What to do when done

Report back in this format:

```
Output file: {{worker_output_absolute_path}}

One-paragraph summary: [what you found, including null results]

Flags for orchestrator attention:
- [anything the orchestrator should know — surprising findings, ambiguities, files that deserve a second pass]

Time spent: [actual runtime]
```

Do NOT continue into synthesis, prose drafting, or adjacent tasks. The orchestrator audits your output and decides next steps.

## 7. What NOT to do (other workers' jobs + out-of-scope)

Other workers in this orchestration:
- **{{other_worker_1_name}}**: {{other_worker_1_scope}} — do NOT do this
- **{{other_worker_2_name}}**: {{other_worker_2_scope}} — do NOT do this

Out-of-scope for EVERY worker (always the orchestrator's job):
- Writing prose or polished deliverables from worker outputs
- Merging findings across workers
- Relitigating the task decomposition
- Modifying the master plan or INDEX.md

If you find yourself wanting to do any of the above, STOP and write the idea into `scope_creep_log.md` in the working directory instead.
