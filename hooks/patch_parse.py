#!/usr/bin/env python3
r"""patch_parse.py - Codex apply_patch text -> per-file operations with reconstructed content.

Codex integration Part 2 (2026-09-07). Codex edits files with one
`apply_patch` call whose only input is the raw patch text:

    *** Begin Patch
    *** Add File: rel/or/abs/path.md
    +line one
    *** Update File: other.md
    *** Move to: renamed.md            (optional)
    @@ optional context header
     context line
    -removed line
    +added line
    *** End of File                    (optional)
    *** Delete File: gone.md
    *** End Patch

The hook family reasons in Claude's shape (Write: file_path + content; Edit:
file_path + old_string + new_string), so the dispatcher turns one patch into
one payload per file through parse() and, for Update File, apply() which
reconstructs the resulting file from the on-disk content (Codex acceptance
criterion: validate the whole patch, not the hunks alone). A hunk that does
not match the file is reported (applied=False, error) and never guessed.

parse(text, cwd=None) -> list of FileOp dicts:
    {"op": "add"|"update"|"delete"|"move", "path": abs, "new_path": abs|None,
     "hunks": [{"header": str, "old": [lines], "new": [lines]}],
     "content": str|None (add: the new file), "before": str|None,
     "after": str|None, "applied": bool, "error": str}
Paths are resolved against cwd (the session cwd from the hook payload) when
relative. Nothing is written to disk here.
"""
import os
import re

MARK = re.compile(r"^\*\*\* (Begin Patch|End Patch|Add File|Delete File|Update File|Move to|End of File)(?::\s*(.*))?\s*$")


def _abs(path, cwd):
    p = (path or "").strip().strip('"').strip("'")
    if not p:
        return p
    if not os.path.isabs(p) and cwd:
        p = os.path.join(cwd, p)
    return os.path.normpath(p)


def parse(text, cwd=None):
    ops = []
    cur = None
    hunk = None
    lines = (text or "").replace("\r\n", "\n").split("\n")
    for raw in lines:
        m = MARK.match(raw)
        if m:
            kind, arg = m.group(1), (m.group(2) or "")
            if kind in ("Begin Patch", "End Patch"):
                cur, hunk = None, None
                continue
            if kind == "Add File":
                cur = {"op": "add", "path": _abs(arg, cwd), "new_path": None, "hunks": [], "content_lines": [],
                       "content": None, "before": None, "after": None, "applied": False, "error": ""}
                ops.append(cur); hunk = None
                continue
            if kind == "Delete File":
                cur = {"op": "delete", "path": _abs(arg, cwd), "new_path": None, "hunks": [], "content": None,
                       "before": None, "after": None, "applied": False, "error": ""}
                ops.append(cur); hunk = None
                continue
            if kind == "Update File":
                cur = {"op": "update", "path": _abs(arg, cwd), "new_path": None, "hunks": [], "content": None,
                       "before": None, "after": None, "applied": False, "error": ""}
                ops.append(cur); hunk = None
                continue
            if kind == "Move to" and cur is not None:
                cur["new_path"] = _abs(arg, cwd)
                cur["op"] = "move"
                continue
            if kind == "End of File":
                if hunk is not None:
                    hunk["eof"] = True
                continue
        if cur is None:
            continue
        if cur["op"] == "add":
            if raw.startswith("+"):
                cur["content_lines"].append(raw[1:])
            elif raw == "":
                cur["content_lines"].append("")
            continue
        if raw.startswith("@@"):
            hunk = {"header": raw[2:].strip(), "old": [], "new": [], "eof": False}
            cur["hunks"].append(hunk)
            continue
        if hunk is None:
            # a hunk body without an @@ line (Codex emits this for single-hunk updates)
            hunk = {"header": "", "old": [], "new": [], "eof": False}
            cur["hunks"].append(hunk)
        if raw.startswith("-"):
            hunk["old"].append(raw[1:])
        elif raw.startswith("+"):
            hunk["new"].append(raw[1:])
        elif raw.startswith(" "):
            hunk["old"].append(raw[1:]); hunk["new"].append(raw[1:])
        elif raw == "":
            hunk["old"].append(""); hunk["new"].append("")
        else:
            # tolerate an unprefixed context line
            hunk["old"].append(raw); hunk["new"].append(raw)
    for op in ops:
        if op["op"] == "add":
            body = op.pop("content_lines")
            op["content"] = "\n".join(body) + ("\n" if body else "")
            op["after"] = op["content"]
            op["applied"] = True
    return ops


def _find(hay, needle, start):
    """Index of the sub-list needle in hay at or after start, else -1 (exact, then whitespace-trimmed)."""
    n = len(needle)
    if n == 0:
        return start
    for i in range(start, len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    stripped = [s.rstrip() for s in needle]
    for i in range(start, len(hay) - n + 1):
        if [s.rstrip() for s in hay[i:i + n]] == stripped:
            return i
    return -1


def apply(op, disk_text=None):
    """Fill before/after for an update/move op from the current file text. Returns the op."""
    if op["op"] not in ("update", "move"):
        return op
    if disk_text is None:
        try:
            with open(op["path"], encoding="utf-8", errors="replace") as f:
                disk_text = f.read()
        except OSError as exc:
            op["error"] = "cannot read %s: %s" % (op["path"], exc)
            return op
    src = disk_text.replace("\r\n", "\n").split("\n")
    # "a\nb\n" splits to ["a", "b", ""]: the trailing "" is the final newline, and
    # `end` is the index just past the last real line. Verified against the real
    # apply_patch tool (Codex, 2026-09-07): an End of File hunk matches the LAST
    # occurrence of its lines, and a hunk with no old lines appends at the end.
    trailing_nl = bool(src) and src[-1] == ""
    end = len(src) - 1 if trailing_nl else len(src)
    out = []
    pos = 0
    for h in op["hunks"]:
        old, new = h["old"], h["new"]
        if not old:
            idx = max(end, pos)
        elif h.get("eof"):
            idx = end - len(old)
            if idx < pos or (src[idx:idx + len(old)] != old
                             and [s.rstrip() for s in src[idx:idx + len(old)]] != [s.rstrip() for s in old]):
                op["error"] = "End of File hunk does not match the file's last %d line(s)" % len(old)
                op["before"] = disk_text
                return op
        else:
            start = pos
            if h.get("header"):
                for i in range(pos, len(src)):
                    if h["header"] in src[i]:
                        start = i
                        break
            idx = _find(src, old, start)
            if idx < 0 and start != pos:
                idx = _find(src, old, pos)
            if idx < 0:
                op["error"] = "hunk does not match the file (%d old lines, first: %r)" % (
                    len(old), (old[0] if old else ""))
                op["before"] = disk_text
                return op
        out.extend(src[pos:idx])
        out.extend(new)
        pos = idx + len(old)
    out.extend(src[pos:])
    op["before"] = disk_text
    op["after"] = "\n".join(out)
    op["applied"] = True
    return op


def edit_strings(op):
    """(old_string, new_string) in Claude's Edit shape: the changed regions only,
    hunks joined by a newline, so the prose and claims gates see what changes."""
    olds = ["\n".join(h["old"]) for h in op.get("hunks", [])]
    news = ["\n".join(h["new"]) for h in op.get("hunks", [])]
    return "\n".join(olds), "\n".join(news)


def parse_and_apply(text, cwd=None):
    ops = parse(text, cwd)
    for op in ops:
        if op["op"] in ("update", "move"):
            apply(op)
        elif op["op"] == "delete":
            op["applied"] = os.path.isfile(op["path"])
            if not op["applied"]:
                op["error"] = "file to delete does not exist"
    return ops


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    txt = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    for o in parse_and_apply(txt):
        o = dict(o)
        for k in ("before", "after", "content"):
            if isinstance(o.get(k), str):
                o[k] = o[k][:120]
        print(json.dumps(o, ensure_ascii=False))
