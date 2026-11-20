"""Shared helpers for the hook tests.

Every hook is exercised the way Claude Code runs it: a subprocess fed the JSON payload on stdin,
judged by exit code, stdout and stderr. State goes to a per-test temp dir through
CLAUDE_HOOK_STATE_DIR, so nothing touches ~/.claude.

Many tests are ports of one long private suite that collected named checks; the Checks helper
keeps that style (each check has a name; a test fails with the list of failed names).
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "hooks")
sys.path.insert(0, HOOKS)

# Environment a developer shell may carry that would change hook behaviour.
_SCRUB = ("CLAUDE_TOOL_INPUT", "CLAUDE_HOOK_AGENT", "CLAUDE_DISPATCH_SKIP", "CLAUDE_HOOK_DUMP_DIR",
          "CLAUDE_RENDER_CHECK_DIRS", "HARNESS_OWNER_NAME", "HARNESS_SHARE_ALLOWLIST",
          "HARNESS_CONTACT_NAMES", "CLAUDE_SNAPSHOT_ROOTS", "CLAUDE_SNAPSHOT_REGISTRY",
          "CLAUDE_REPLY_FRAME_CONFIG", "CLAUDE_TURN_END_GATE_CONFIG")


def hook(name):
    return os.path.join(HOOKS, name)


def base_env(state, **extra):
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env["CLAUDE_HOOK_STATE_DIR"] = state
    env["PYTHONIOENCODING"] = "utf-8"
    env.update({k: v for k, v in extra.items() if v is not None})
    return env


def run_hook(script, payload, state, **env_extra):
    """(returncode, stdout, stderr) for one hook run."""
    p = subprocess.run([sys.executable, script], input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=base_env(state, **env_extra), timeout=60)
    return p.returncode, p.stdout, p.stderr


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tent(typ, content, ts, **kw):
    """One transcript entry in Claude Code's JSONL shape."""
    d = {"type": typ, "isSidechain": False, "timestamp": ts,
         "message": {"role": "user" if typ == "user" else "assistant", "content": content}}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


def tool_use(name, **inp):
    return {"type": "tool_use", "id": "t" + name, "name": name, "input": inp}


class Checks:
    def __init__(self):
        self.failed = []
        self.passed = 0

    def __call__(self, name, cond, detail=""):
        if cond:
            self.passed += 1
        else:
            self.failed.append(name + ("  [%s]" % detail if detail else ""))

    def done(self):
        assert not self.failed, "%d check(s) failed:\n  " % len(self.failed) + "\n  ".join(self.failed)


@pytest.fixture
def check():
    return Checks()


@pytest.fixture
def state(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return str(d)
