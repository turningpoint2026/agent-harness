#!/usr/bin/env python3
"""proc_quiet.py - subprocess wrappers that never open a console window.

Why (2026-09-07, the owner: "there is terminal firing rapidly, suppress them"):
hook_dispatch.py started its children with CREATE_NEW_CONSOLE + SW_HIDE so
that grandchild console programs (git, tasklist, pandoc) would inherit one
hidden console. Windows Terminal 1.24 was the default console host on the
author's machine (the delegation values under HKCU Console Startup are unset, so
Windows picks it) and it ignores SW_HIDE (microsoft/terminal issues 12464
and 15311): every child opened a visible Windows Terminal window, five per
Bash call, two per Read, four per turn end.

Rule now: no process in the hook tree owns a console. Every launch site
reachable from a hook goes through quiet_run / quiet_popen, which add
CREATE_NO_WINDOW (a console application run without a console window). A
child started this way has no console, so any grandchild it launches with a
plain subprocess.run would allocate a fresh visible one: that is why the
wrappers are used at every level, not only in the dispatcher. Captured
output and pipes work as before; a call that relied on the child printing
straight to an inherited console must capture and print instead.

quiet_popen(detached=True) is for the weekly self-test runner, which must
outlive the hook: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (Windows
ignores CREATE_NO_WINDOW when DETACHED_PROCESS is set; a detached process
has no console either, and its own children must use CREATE_NO_WINDOW).

Off Windows the wrappers are plain subprocess.run / Popen.
"""
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def quiet_kwargs(kw, detached=False):
    """Add the no-console creation flags to a subprocess kwargs dict (Windows only)."""
    if os.name == "nt":
        flags = kw.get("creationflags", 0) or 0
        if detached:
            flags |= DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            flags |= CREATE_NO_WINDOW
        kw["creationflags"] = flags
        if "startupinfo" not in kw:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            kw["startupinfo"] = si
    return kw


def quiet_run(cmd, **kw):
    """subprocess.run without a console window."""
    return subprocess.run(cmd, **quiet_kwargs(kw))


def quiet_popen(cmd, detached=False, **kw):
    """subprocess.Popen without a console window; detached=True for a runner that must outlive the caller."""
    return subprocess.Popen(cmd, **quiet_kwargs(kw, detached=detached))
