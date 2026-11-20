# Agent harness for Claude Code and Codex

Rules and checks that govern two coding agents, Claude Code and Codex, in my academic research and
operations work. Each check exists because an agent failed in a logged, specific way; the code
comments name the failure, and most checks carry a replay over saved transcripts that measured how
often they would fire before they shipped.

This repository is a cleaned subset of a private harness (32 hook scripts behind 4 wired commands,
50 Python files and 17,632 lines in the private hook folder, a private suite of 376 passing checks at
the 2026-11-07 change). It contains 9 hook components and 3 skills, with their tests.

## How it works

Claude Code (and Codex, through its hooks.json) runs a command at four events: when the user
submits a prompt (`UserPromptSubmit`), before a tool call (`PreToolUse`), after a tool call
(`PostToolUse`), and when the agent ends its turn (`Stop`). A hook reads a JSON payload on stdin.
Exit code 2 blocks the tool call (or, at `Stop`, sends the agent back to revise its reply) with the
hook's stderr as the reason.

- One command per event. `hooks/hook_dispatch.py <event>` reads `hook_registry.json` and runs every
  matching hook script as a console-less child process, in parallel, then folds their results into
  one answer for the host.
- Two hosts. For Codex the dispatcher translates payloads into the shapes the hooks were written for
  (shell calls into `Bash`, `apply_patch` into per-file `Write`/`Edit`, Codex rollouts into a
  Claude-shaped transcript), and returns blocks in the JSON form Codex documents, because Codex
  ignored a bare exit 2.
- Every hook fails open. An internal error exits 0, because a crashed gate must not stop the agent's
  tools. The cost of that choice is listed under Limits.
- Bypasses are user-only and conspicuous. Each gate names the phrase the user types to lift it; an
  agent can still defeat a local gate with shell access, so the design goal is that compliance is the
  cheap path and a bypass shows in the transcript.

## Components

| Component | What it catches | Failure class it came from | Test |
|---|---|---|---|
| `hooks/hook_dispatch.py` with `proc_quiet.py`, `transcript_io.py`, `patch_parse.py` | Not a gate: runs the gates once per event and translates Codex payloads | One console window per hook command made typing impossible; Codex ignored exit 2; Codex decoded payloads in the wrong encoding | `python -m pytest tests/test_dispatch.py` |
| `hooks/deploy.py` | Refuses to deploy unless the test suite passes; refuses to silently drop a live hook; `--verify` reports drift | A deployer that had drifted to 6 hooks while 15 were live would have deleted 10 gates without a word | `python -m pytest tests/test_deploy.py` |
| `hooks/reply_frame_gate.py` | A final reply that hands the user an action (resume, paste, approve, choose) without the one line format reserved for that | Replies said "nothing needs you" while a handoff waited for the user to launch it; long status boilerplate the user had to read past | `python -m pytest tests/test_reply_frame_gate.py tests/test_reply_frame_redteam.py` |
| `hooks/turn_end_gate.py` | R3: a reply announcing a message or launch ("Dispatching three verification windows") that no tool call made. R1 (completion claim with no read-back) and R2 (autopilot ending on a question) ship shadow-only | An agent claimed it had asked another session, or launched workers, when it had not | `python -m pytest tests/test_turn_end_gate.py` |
| `hooks/external_comms_gate.py` with `comms_greenlight.py` | Email drafts and sends, calendar writes, Slack posts, and file writes or shell copies into shared folders, unless the user typed an approval phrase in this session within 30 minutes | An agent drafted an outbound email before the work was reviewed; agent-written text was once read as the user's approval | `python -m pytest tests/test_comms_gates.py` |
| `hooks/claims_ledger_gate.py` | A deliverable line that asserts a number, an attribution or a status with no source named and no uncertainty label; in handoff files, a "still running" claim with no named check | Fabricated bylines and unsourced figures in reports that carried plausible citations elsewhere | `python -m pytest tests/test_claims_ledger_gate.py` |
| `hooks/synthesis_coverage_guard.py` with `read_ledger.py`, `coverage_bypass_listener.py` | A synthesis document written before every file in the project's corpus list was read in full this session | A results report written from about 6 of about 20 findings documents, silently omitting an analysis arm | `python -m pytest tests/test_coverage_gate.py` |
| `hooks/usage_hard_stop.py` | A session over its token budget, and (since 2026-11-07) a single subagent over its own cap | Two workflow lanes burned about 36M cache-write tokens in under 2 hours; later, single helpers burned up to 51.6M re-reading images | `python -m pytest tests/test_usage_hard_stop.py` |
| `hooks/session_mode.py` with `discuss_mode_gate.py`, `correction_families.py` | Writes and worker spawns while the user has asked to discuss first; the mode is set only by the user's own words, never by agent text | Agents executed when told to discuss and stopped when told to keep going | `python -m pytest tests/test_session_mode.py` |
| `skills/loop-improve/` | A multi-agent revision loop: reviewer arms with different visibility, blind extraction plus an adversarial verifier, an arbitrator, a judge, and a reader stage that asks what a reader would skip or miss | A verifier asked only whether a claim is plausible tends to agree with it; a skeptical verifier also waves through true but useless content | Reference (Markdown instructions; `scripts/sum_usage.py` is a utility) |
| `skills/orchestrate-workers/` | Scaffolding for parallel worker sessions that coordinate only through files: self-contained instruction files, one-line launch prompts, mechanical and adversarial audit workers | Re-launched workers regenerated outputs already on disk; null results from partly read PDFs passed as absence of evidence | Reference |
| `skills/verify-claims/` | The semantic half of the claims gate: read-only subagents try to refute each claim against its named source and write a claims ledger | A cited source did not say what the report claimed | Reference |

Run everything: `python -m pytest` from the repository root (46 tests, about one minute; each test
runs the hooks as subprocesses the way the host does, with state in a temp folder).

## Measured results

All figures come from the author's evidence summary of the private harness (n = 1 user, one machine,
February to November 2026). "Replay" means a rule run over saved transcripts before it shipped.

What worked:

- False "sent" or "launched" claims (turn-end R3): replay over 2,506 saved turns gave 8 would-bounce
  turns (0.32 percent), 7 of them real, including both known incidents. Live log: 2 bounces.
- Agent text forging approval: on 2026-08-09 a workflow's own summary containing "green light" stamped
  a live 30-minute approval for email and calendar. "A gate an agent can trip by writing a sentence is
  not a gate." Machine-origin text can no longer stamp. The shell-write extension, replayed over
  72,035 saved calls, flagged 5, all real writes into a share.
- Reply gate red team: 67 crafted hand-backs beat the phrase-list gate 66 times (recall 1 of 67).
  The structural rebuild caught 53 of 67; the sanitized copy in `tests/redteam_crafted.py` measures
  54 of 67 here. Live since 2026-10-31: 1,167 passes, 102 notes, 41 bounces (3.1 percent of 1,310
  checked replies); of the 41, 31 retries fixed the reply, 9 were declined by the agent as false
  bounces, 1 stayed wrong.
- Runaway helpers: on 2026-11-07 five helper roles cost 145.5M tokens against about 16.7M for their
  siblings, one at 51.6M with no output file written. A replay over 505 subagent transcripts showed
  an 8M per-helper cap would have stopped 10 of them; the author chose that cap.
- The budget warning first went out only as a user-facing message and was ignored in 23 warned
  sessions; it reached the model only after it was also sent as model context.
- Session modes: 24 of 25 "keep going" corrections came from autonomous campaigns, and 11 of 11
  "discuss first" corrections from writing work, while the always-loaded rules had six ask-first
  rules and one proceed rule, none conditional. Modes now come from the user's own signals.
- Revision loop: a 19-agent run cost at most 2.11M tokens, was approved in one feedback pass, and the
  author found zero claim errors. An arbitrator overturned 2 verifier verdicts by opening the primary
  log the verifier had skipped.

What did not work, or is not known:

- R1, "done" without a read-back, failed its pre-set pass mark (about 15.6 percent of turns would
  bounce; recall 7 of 25 known cases). It ships shadow-only and has logged 667 unreviewed firings.
- The first claims gate flagged 17 percent of all lines and would have blocked 194 of 200 real
  deliverables. It was narrowed to claims about the world, so tables and numbers presented as data
  pass unchecked; `/verify-claims` is the only check they get.
- On the crafted legitimate lines, the rebuilt reply gate still false-bounces 23 of 45.
- Relayed approvals: on 2026-10-27 and 10-28 messages relayed between agent sessions quoting
  "GO SHARE" produced seven stamps in sessions the user never addressed. Nothing was sent, and this
  copy of `comms_greenlight.py` does not yet refuse relayed peer messages.
- An answer given inside a false premise: every number in it was correct and sourced, so neither the
  claims gate nor `/verify-claims` could see the error, which sat in the framing. Only a text rule
  covers it.
- Guards that looked wired and did nothing: a non-ASCII byte in a block message failed open; a gate
  passed 9 of 9 unit fixtures while raising NameError on every real turn (Codex found it by reading
  the source); Codex hooks decoded payloads in the wrong encoding for five days (31 rows, 0 passes,
  15 of 16 bounces false); a safety flag excluded exactly the failing population for 17 days while
  the log showed passes.
- The human approver is a failure point too: the user-only budget was raised from 25M to 40M, found
  15 days later at 53M and disarmed; on 2026-11-06 it went from 25M to 150M in three steps under one
  standing approval, and was restored to 25M the next day.
- The headline outcome did not move: the measured weekly correction rate (user corrections of the
  agent, as tagged in the transcripts) stayed between 10.0 and 15.3 percent while the harness grew
  from 15 to 27 hook commands. Reply length
  fell (median 1,846 characters before the reply rules, 374 on 2026-11-16), but the task mix and an
  interface change confound that.
- The always-loaded rule text was cut from 72,779 to 22,287 characters in a redesign and has grown
  back to 34,424.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `CLAUDE_HOOK_STATE_DIR` | all hooks | State folder (default `~/.claude/state`, or `~/.claude/hook_state` for the ledger family) |
| `HARNESS_OWNER_NAME` | reply_frame_gate | The person the agent hands actions to (default "the user") |
| `HARNESS_SHARE_ALLOWLIST` | external_comms_gate | Path fragments, `os.pathsep`-separated, whose "shared" is a name and not a live share |
| `HARNESS_CONTACT_NAMES` | comms_greenlight | Comma-separated names that trigger the work-before-comms reminder |
| `HARNESS_PROJECT_DIR_PREFIX` | claims_ledger_gate | Folder-name prefix that marks a project root (default `000PROJECT`); a `Claude_ProjectMemory_*.md` file also marks one |
| `CLAUDE_SNAPSHOT_ROOTS` | claims_ledger_gate | JSON list of roots under which handoff files are gated |
| `CLAUDE_REPLY_FRAME_CONFIG`, `CLAUDE_TURN_END_GATE_CONFIG`, `CLAUDE_SETTINGS_PATH` | gates, deploy | Override the JSON config files beside the hooks and the settings file |
| `CLAUDE_DISPATCH_SKIP`, `CLAUDE_HOOK_DUMP_DIR` | hook_dispatch | Skip named scripts; record raw and translated payloads for diagnosis |
| `HARNESS_MASTER_MD`, `HARNESS_LOADED_MD` | deploy, hook_dispatch | Optional: copy a master rule file over the loaded one on every prompt |

Install on a machine: `python hooks/deploy.py --dry-run` runs the tests and prints the planned
`settings.json` hooks block; `python hooks/deploy.py --write` writes `hooks/hook_registry.json` and
replaces only the `hooks` key of `~/.claude/settings.json` (after a backup). The scripts were developed
on Windows; the tests run there and use only the Python standard library plus pytest.

## Limits

- No model training, internals or weights. Every guard is deterministic code, rule text or prompt
  structure around a deployed model.
- One user, one machine, about nine months. Failure classes were tagged by a single model per chunk
  of transcripts; the 5 turns tagged twice disagreed in 3 of 5.
- Self-built evaluation: replays, red teams and fixtures were run by agents inside the same harness,
  with no outside review.
- Every gate fails open, and an agent with shell access can bypass any of them.
- Not included: the always-loaded rule file, the mistake registers (per-task logs of past errors with
  the author's rulings), session logs, usage data, and hooks tied to private folders or personal
  records.

## Why I built this

<!-- Yilang writes this section -->
