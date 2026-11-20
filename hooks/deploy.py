#!/usr/bin/env python3
"""Test-gated deployer for this hook family (adapted from the private deploy_hooks.py).

    python hooks/deploy.py --dry-run   # run the tests, print the planned registry and hooks block
    python hooks/deploy.py --verify    # compare the planned hooks block with the live settings.json
    python hooks/deploy.py --write     # run the tests, then write hook_registry.json and the
                                       # "hooks" key of settings.json (backup first)

What it does:
  1. Checks every script named in HOOKS exists beside this file; ABORTS if any is missing
     (a hook pointing at a missing file fails open on every call, silently).
  2. Runs the test suite (python -m pytest <repo>/tests -q); REFUSES to write unless it passes.
  3. Writes hook_registry.json beside this file: the per-event rows hook_dispatch.py reads.
  4. Plans one settings.json command per event ("python hook_dispatch.py <event>"), so a tool call
     starts one process per event instead of one per hook (Windows showed a console per command).
  5. With --write: backs up settings.json, refuses to silently drop a live hook (set
     DEPLOY_HOOKS_FORCE=1 when the removal is intended), and replaces ONLY the "hooks" key.

Why the refusal to drop hooks: on 2026-08-07 the private deployer had drifted to 6 hooks while the
live settings held 15; running it would have deleted 10 gates without a word. --verify exists so
that drift is detected instead of latent; identity is (event, matcher, script basename), so a
moved folder is reported separately from an added or dropped hook.

Settings path: CLAUDE_SETTINGS_PATH, else ~/.claude/settings.json.
Optional prompt-time copy of a master rule file over the loaded one: set HARNESS_MASTER_MD and
HARNESS_LOADED_MD; the registry's "sync" pair then makes hook_dispatch.py copy it every prompt.
"""

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SETTINGS = os.environ.get("CLAUDE_SETTINGS_PATH") or os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
REGISTRY_NAME = "hook_registry.json"
PY = "python" if os.name == "nt" else "python3"
EVENT_ORDER = ["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]

COMMS_MATCHER = (
    "Edit|Write|MultiEdit"
    "|mcp__claude_ai_Gmail__create_draft"
    "|mcp__claude_ai_Gmail__update_draft"
    "|mcp__claude_ai_Gmail__send"
    "|mcp__claude_ai_Google_Calendar__create_event"
    "|mcp__claude_ai_Google_Calendar__update_event"
    "|mcp__claude_ai_Google_Calendar__delete_event"
    "|mcp__claude_ai_Google_Calendar__respond_to_event"
    "|slack"
    "|Bash|PowerShell"   # 2026-10-01: shell writes into OneDrive / shared folders
)

# (event, matcher, script): the hooks this repository ships. Matchers keep Claude Code's
# semantics (a regex searched in the tool name; empty = every tool).
HOOKS = [
    ("UserPromptSubmit", "", "comms_greenlight.py"),
    ("UserPromptSubmit", "", "coverage_bypass_listener.py"),
    ("UserPromptSubmit", "", "session_mode.py"),
    ("PreToolUse", "Edit|Write|MultiEdit", "claims_ledger_gate.py"),
    ("PreToolUse", "Write", "synthesis_coverage_guard.py"),
    ("PreToolUse", COMMS_MATCHER, "external_comms_gate.py"),
    ("PreToolUse", "Task|Agent|Workflow|Read|Bash", "usage_hard_stop.py"),
    ("PreToolUse", "Write|Edit|MultiEdit|NotebookEdit|Agent|Task|Workflow", "discuss_mode_gate.py"),
    ("PostToolUse", "Read", "read_ledger.py"),
    ("Stop", "", "turn_end_gate.py"),
    ("Stop", "", "reply_frame_gate.py"),
]
# Hooks that read Claude-only transcript shapes never run for Codex.
CODEX_SKIP = {"turn_end_gate.py", "usage_hard_stop.py"}


def fwd(p):
    return p.replace("\\", "/")


def missing_scripts():
    return [s for _, _, s in HOOKS if not os.path.isfile(os.path.join(HERE, s))]


def registry_dict(target_dir=HERE):
    events = {e: [] for e in EVENT_ORDER}
    for event, matcher, script in HOOKS:
        events[event].append({"matcher": matcher, "script": script,
                              "cmd": [PY, fwd(os.path.join(target_dir, script))],
                              "agents": ["claude"] if script in CODEX_SKIP else ["claude", "codex"]})
    reg = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "hooks_dir": fwd(target_dir),
           "events": {e: v for e, v in events.items() if v}}
    src, dst = os.environ.get("HARNESS_MASTER_MD"), os.environ.get("HARNESS_LOADED_MD")
    reg["sync"] = {"src": fwd(src), "dst": fwd(dst)} if src and dst else {}
    return reg


def write_registry(target_dir=HERE):
    p = os.path.join(target_dir, REGISTRY_NAME)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(registry_dict(target_dir), f, indent=1, ensure_ascii=False)
        f.write("\n")
    return p


def build_hooks(target_dir=HERE):
    """The settings.json "hooks" block: one dispatcher command per event that has hooks."""
    disp = fwd(os.path.join(target_dir, "hook_dispatch.py"))
    interp = "pythonw" if os.name == "nt" and shutil.which("pythonw") else PY
    return {e: [{"matcher": "", "hooks": [{"type": "command", "command": "%s %s %s" % (interp, disp, e)}]}]
            for e in EVENT_ORDER if any(ev == e for ev, _, _ in HOOKS)}


def commands_of(block):
    rows = []
    for event, groups in (block or {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                rows.append((event, g.get("matcher", ""), h.get("command", "")))
    return rows


def identity(row):
    """(event, matcher, script basename plus any event argument): a relocated folder is not drift."""
    event, matcher, cmd = row
    tokens = cmd.replace("\\", "/").split()
    i = next((k for k, t in enumerate(tokens) if t.lower().endswith(".py")), None)
    script = os.path.basename(tokens[i]) if i is not None else (tokens[-1] if tokens else "")
    args = " ".join(tokens[i + 1:]) if i is not None else ""
    return (event, matcher, (script + " " + args).strip())


def run_tests():
    print("Running test suite...")
    return subprocess.run([sys.executable, "-m", "pytest", os.path.join(REPO, "tests"), "-q"]).returncode


def verify():
    if not os.path.isfile(SETTINGS):
        print("ABORT: %s not found." % SETTINGS)
        return 1
    with open(SETTINGS, encoding="utf-8") as f:
        live = json.load(f).get("hooks", {})
    planned = {identity(r): r for r in commands_of(build_hooks())}
    current = {identity(r): r for r in commands_of(live)}
    only_live = sorted(set(current) - set(planned))
    only_planned = sorted(set(planned) - set(current))
    repathed = [k for k in set(planned) & set(current) if current[k][2] != planned[k][2]]
    print("live: %d hook command(s) | planned: %d" % (len(current), len(planned)))
    for e, m, s in only_live:
        print("  - [%s] %s :: %s   (in settings.json only; --write WOULD DELETE it)" % (e, m or "*", s))
    for e, m, s in only_planned:
        print("  + [%s] %s :: %s   (planned only; --write would add it)" % (e, m or "*", s))
    if repathed:
        print("PATH DIFFERENCE on %d command(s): same set, different location." % len(repathed))
    if not only_live and not only_planned:
        print("HOOK SET IN SYNC" + (" (paths differ)" if repathed else ""))
        return 0 if not repathed else 2
    print("SET DRIFT - decide which side is stale before deploying.")
    return 1


def main(argv):
    gone = missing_scripts()
    if gone:
        print("ABORT: referenced hook script(s) missing: " + ", ".join(gone))
        return 1
    if "--verify" in argv:
        return verify()
    if run_tests() != 0:
        print("\nABORT: tests failed - nothing written.")
        return 1
    hooks = build_hooks()
    print("\nPlanned registry rows: %d hooks behind %d dispatcher commands" % (len(HOOKS), len(hooks)))
    print(json.dumps(hooks, indent=2))
    if "--write" not in argv:
        print("\nDry run (pass --write to apply): nothing written.")
        return 0
    if not os.path.isfile(SETTINGS):
        print("\nABORT: %s not found. Create settings.json first." % SETTINGS)
        return 1
    with open(SETTINGS, encoding="utf-8") as f:
        cfg = json.load(f)
    losing = {identity(r) for r in commands_of(cfg.get("hooks", {}))} - {identity(r) for r in commands_of(hooks)}
    if losing and os.environ.get("DEPLOY_HOOKS_FORCE") != "1":
        print("\nABORT: this deploy would REMOVE live hook(s):")
        for e, m, c in sorted(losing):
            print("  - [%s] %s :: %s" % (e, m or "*", c))
        print("Re-run with DEPLOY_HOOKS_FORCE=1 if the removal is intended.")
        return 1
    print("Registry written: " + write_registry())
    backup = SETTINGS + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(SETTINGS, backup)
    cfg["hooks"] = hooks
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print("Backup: %s\nUpdated: %s (hooks key only; other keys preserved)" % (backup, SETTINGS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
