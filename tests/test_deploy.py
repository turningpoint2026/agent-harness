"""deploy.py: registry and settings planning, drift detection. The test run itself is not invoked here
(deploy.py runs this suite before writing anything)."""
import json
import os

from conftest import HOOKS, load_module


def test_registry_and_verify(tmp_path, monkeypatch, check):
    dep = load_module("deploy_t", os.path.join(HOOKS, "deploy.py"))
    check("every script named in HOOKS exists", dep.missing_scripts() == [], str(dep.missing_scripts()))
    reg = dep.registry_dict(str(tmp_path))
    rows = [r for v in reg["events"].values() for r in v]
    check("the registry carries one row per HOOKS entry, with the matcher and a command per row",
          len(rows) == len(dep.HOOKS) and all(r["cmd"][1].endswith(r["script"]) for r in rows))
    check("Claude-transcript hooks are marked Claude-only",
          all(r["agents"] == ["claude"] for r in rows if r["script"] in dep.CODEX_SKIP))
    hooks = dep.build_hooks(str(tmp_path))
    check("one dispatcher command per event", all(len(v) == 1 and "hook_dispatch.py " + e in v[0]["hooks"][0]["command"] for e, v in hooks.items()))
    check("identity ignores the folder but keeps the event argument",
          dep.identity(("Stop", "", "python /a/hook_dispatch.py Stop")) == dep.identity(("Stop", "", "python /b/hook_dispatch.py Stop"))
          and dep.identity(("Stop", "", "python /a/hook_dispatch.py Stop")) != dep.identity(("Stop", "", "python /a/hook_dispatch.py PreToolUse")))

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "x", "hooks": dep.build_hooks()}), encoding="utf-8")
    monkeypatch.setattr(dep, "SETTINGS", str(settings))
    check("--verify reports a matching hook set as in sync", dep.main(["--verify"]) == 0)
    live = dep.build_hooks()
    live["SessionStart"] = [{"matcher": "", "hooks": [{"type": "command", "command": "python /x/other_hook.py"}]}]
    settings.write_text(json.dumps({"hooks": live}), encoding="utf-8")
    check("--verify flags a live hook the plan would delete", dep.main(["--verify"]) == 1)
    check.done()
