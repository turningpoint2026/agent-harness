#!/usr/bin/env python3
"""Synthesis coverage gate (v2, 2026-08-06).

PreToolUse hook on Edit / Write / MultiEdit. Replaces the v1 one-shot
tripwire, whose block was cleared by resubmitting the same call and which
never enumerated anything despite CLAUDE_GLOBAL.md claiming it did.

Two modes, decided by whether the project root has a CORPUS.md manifest:

MANIFEST MODE (<root>/CORPUS.md exists) — hard gate.
    The synthesis write is blocked until every file listed in CORPUS.md
    appears in this session's Read-tool ledger (written by read_ledger.py)
    as a full read. Blocks repeat on EVERY attempt — no marker, no retry
    escape. The only bypass is user authorization: the user includes
    "override coverage gate" in a prompt (coverage_bypass_listener.py
    records the per-session flag).

TRIPWIRE MODE (no CORPUS.md) — one block per session, WITH enumeration.
    The block message enumerates findings-shaped documents under the
    project root (2 levels down), so the checklist arrives with the actual
    list, and explains how to create CORPUS.md to enable the hard gate.
    Retrying passes, as in v1. Rationale: hard-gating on a raw glob would
    demand reading textbook notes and worker instructions before any
    report write — friction that gets guardrails disabled.

Trigger: synthesis-shaped basename (v1 regex) OR findings-language content
(>= 3 matches and >= 1500 chars). Internal scratch files skipped by name.

CORPUS.md format: one path per line (relative to project root or
absolute), optionally as a markdown list item or link. Lines without a
file extension are ignored as prose; listed files missing on disk are
reported in the block message.

Design notes (2026-08-06 session):
  - Verifies behavior (Read-tool ingestion), not self-reported artifacts:
    quote-anchored read logs can be harvested mechanically without reading.
  - Friction + legibility, not security. An agent with shell access can
    defeat any local gate; the goal is that honest compliance is the
    cheapest path and any bypass is conspicuous in the transcript.
  - What this gate can fix: coverage omission (the 2026-07-21 class of
    failure). What it cannot fix: mischaracterization of material that WAS
    read — comprehension is not machine-checkable.

State (ledger, flags, markers) lives in a LOCAL dir, never cloud-synced:
~/.claude/hook_state/  (override for tests: CLAUDE_HOOK_STATE_DIR)

Origin: 2026-07-21 — a faculty-facing "preliminary results" report was
written from ~6 of ~20 findings documents, silently omitting an entire
analysis arm. Companion rule: CLAUDE_GLOBAL.md, ALWAYS APPLY #1
("Act on checked state" — rule renumbered 2026-08-06, formerly #6).
"""

import json
import os
import re
import sys

PROJECT_MEMORY_RE = re.compile(r"^Claude_ProjectMemory_.*\.md$", re.IGNORECASE)
SYNTH_NAME_RE = re.compile(
    r"(report|results|summary|findings|synthesis|overview|memo|prelim)",
    re.IGNORECASE,
)
# Internal scratch / infrastructure files — never gated.
SKIP_NAME_RE = re.compile(
    r"(session_state|handoff|scope_creep|index|instructions|readme|corpus"
    r"|read_log|todo|proposal|template|checklist)",
    re.IGNORECASE,
)
CONTENT_RE = re.compile(
    r"("
    r"(our|the|key|main|preliminary)\s+(findings|results)"
    r"|we\s+(found|find|document|observe|estimate)"
    r"|##\s*(findings|results|key\s+takeaways)"
    r"|the\s+(data|results|analysis|evidence)\s+(show|shows|suggest|suggests|indicate|indicates)"
    r")",
    re.IGNORECASE,
)
EXTENSIONS = (".md", ".tex", ".docx")
PATH_LINE_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")

MIN_NAME_TRIGGER_CHARS = 400
MIN_CONTENT_TRIGGER_CHARS = 1500
MIN_CONTENT_MATCHES = 3
GLOB_DEPTH = 2
NOISE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p))).replace("\\", "/")


def safe(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s))[:80]


def state_dir():
    return os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "hook_state"
    )


def read_payload():
    raw = os.environ.get("CLAUDE_TOOL_INPUT")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def find_project_root(file_path):
    """Walk up from the target file looking for Claude_ProjectMemory_*.md."""
    d = os.path.dirname(os.path.abspath(file_path))
    for _ in range(10):
        try:
            names = os.listdir(d)
        except OSError:
            return None
        if any(PROJECT_MEMORY_RE.match(n) for n in names):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def get_content(tool_input):
    content = tool_input.get("content") or tool_input.get("new_string") or ""
    if not content and isinstance(tool_input.get("edits"), list):
        content = "\n".join(
            e.get("new_string", "") for e in tool_input["edits"] if isinstance(e, dict)
        )
    return content


def is_synthesis_write(base, content):
    if SKIP_NAME_RE.search(base):
        return False
    if SYNTH_NAME_RE.search(base) and len(content) >= MIN_NAME_TRIGGER_CHARS:
        return True
    if len(content) >= MIN_CONTENT_TRIGGER_CHARS and len(
        CONTENT_RE.findall(content)
    ) >= MIN_CONTENT_MATCHES:
        return True
    return False


def parse_corpus(root):
    """Return (entries, missing) from <root>/CORPUS.md, or None if absent."""
    path = os.path.join(root, "CORPUS.md")
    if not os.path.isfile(path):
        return None
    entries, missing = [], []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = s.lstrip("-*+ ").strip().strip("`")
        m = re.match(r"\[[^\]]*\]\(([^)]+)\)", s)
        if m:
            s = m.group(1).strip()
        if not PATH_LINE_RE.search(s):
            continue  # prose line, not a path
        full = s if os.path.isabs(s) else os.path.join(root, s)
        if os.path.isfile(full):
            entries.append(full)
        else:
            missing.append(s)
    return entries, missing


def _same_hash(path, digest):
    """True when the file's current sha256 equals the ledger's (fail open on read errors)."""
    try:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest() == digest
    except OSError:
        return True


def load_full_reads(sid):
    ledger = os.path.join(state_dir(), f"read_ledger_{safe(sid)}.jsonl")
    full = set()
    try:
        with open(ledger, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("full") and e.get("path"):
                    # Codex integration part 2 (2026-09-07, Codex review): a read
                    # certifies the content it hashed, so a corpus file changed since the
                    # read is unread again; a Codex shell read without a hash never
                    # counts as full (read_ledger writes it partial; older rows are
                    # held to the same rule here).
                    if e.get("via") == "codex-shell" and not e.get("sha256"):
                        continue
                    if e.get("sha256") and not _same_hash(e["path"], e["sha256"]):
                        continue
                    full.add(e["path"])
    except OSError:
        pass
    return full


def glob_findings_docs(root, exclude_norm):
    """Findings-shaped .md files under root, GLOB_DEPTH levels down."""
    found = []
    root = os.path.abspath(root)
    base_depth = root.rstrip("\\/").count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in NOISE_DIRS]
        if dirpath.rstrip("\\/").count(os.sep) - base_depth >= GLOB_DEPTH:
            dirnames[:] = []
        for n in filenames:
            if not n.lower().endswith(".md"):
                continue
            if SKIP_NAME_RE.search(n) or PROJECT_MEMORY_RE.match(n):
                continue
            if SYNTH_NAME_RE.search(n):
                p = os.path.join(dirpath, n)
                if norm(p) not in exclude_norm:
                    found.append(p)
    return sorted(found)


def block(lines):
    for line in lines:
        print(line, file=sys.stderr)
    sys.exit(2)


def main():
    payload = read_payload()
    tool_input = payload if "file_path" in payload else payload.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    base = os.path.basename(file_path)
    if not base.lower().endswith(EXTENSIONS):
        sys.exit(0)

    content = get_content(tool_input)
    if not is_synthesis_write(base, content):
        sys.exit(0)

    root = find_project_root(file_path)
    if root is None:
        sys.exit(0)

    sid = payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "nosession"

    # User-authorized bypass (written only by coverage_bypass_listener.py).
    if os.path.isfile(
        os.path.join(state_dir(), f"coverage_bypass_{safe(sid)}.flag")
    ):
        sys.exit(0)

    corpus = parse_corpus(root)
    target_norm = norm(file_path)

    if corpus is not None:
        # ---------------- MANIFEST MODE: hard gate ----------------
        entries, missing = corpus
        enforced = {norm(p): p for p in entries if norm(p) != target_norm}
        full_reads = load_full_reads(sid)
        unread = sorted(
            orig for n, orig in enforced.items() if n not in full_reads
        )
        if not unread and not missing:
            sys.exit(0)
        if not unread and missing:
            # Coverage met; only warn about stale manifest entries, don't gate.
            sys.exit(0)

        n_read = len(enforced) - len(unread)
        extra = glob_findings_docs(root, set(enforced) | {target_norm})
        not_in_manifest = [p for p in extra if norm(p) not in enforced]

        msg = [
            f"[synthesis-coverage-gate] BLOCKED: {base} is a synthesis-shaped "
            f"document, and this session has NOT fully read the project corpus.",
            f"Manifest: {os.path.join(root, 'CORPUS.md')}",
            f"Coverage this session: {n_read}/{len(enforced)} corpus files "
            f"fully read via the Read tool.",
            "",
            "UNREAD — read each with the Read tool (no limit/offset), then retry:",
        ]
        msg += [f"  - {p}" for p in unread]
        if missing:
            msg += ["", "Manifest entries missing on disk (fix CORPUS.md):"]
            msg += [f"  - {p}" for p in missing]
        if not_in_manifest:
            msg += [
                "",
                "Findings-shaped files on disk but NOT in CORPUS.md "
                "(not enforced — tell the user; they may belong in the manifest):",
            ]
            msg += [f"  - {p}" for p in not_in_manifest]
        msg += [
            "",
            "This gate blocks on every attempt until coverage is complete.",
            "Bypass is USER-ONLY: the user must say \"override coverage gate\".",
            "Do not work around this gate via Bash/Python — ask the user instead.",
        ]
        block(msg)

    # ---------------- TRIPWIRE MODE: one block, with enumeration ----------------
    marker = os.path.join(state_dir(), f"coverage_ack_{safe(sid)}.flag")
    if os.path.exists(marker):
        sys.exit(0)
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(norm(file_path) + "\n")
    except OSError:
        pass

    docs = glob_findings_docs(root, {target_norm})
    msg = [
        f"[synthesis-coverage-guard] {base} is a synthesis-shaped document "
        f"under a project folder with NO CORPUS.md manifest.",
        f"Project root: {root}",
        "",
        f"Findings-shaped documents found ({len(docs)}, {GLOB_DEPTH} levels down "
        "— check project memory for arms stored elsewhere):",
    ]
    msg += [f"  - {p}" for p in docs] if docs else ["  (none matched)"]
    msg += [
        "",
        "Checklist (CLAUDE_GLOBAL.md ALWAYS APPLY #1, Syntheses bullet):",
        "  1. Corpus enumerated (list above + project memory's folder map)?",
        "  2. Corpus read IN FULL this session (syntheses are entry points, "
        "not substitutes)?",
        "  3. Coverage stated in the deliverable (covers/excludes)?",
        "",
        "To make this a hard gate: create CORPUS.md at the project root, one "
        "findings-doc path per line (relative to root). Then writes like this "
        "one are blocked until every listed file is fully read in-session.",
        "If all three hold (or this is not a synthesis of prior work), retry — "
        "tripwire mode fires once per session.",
    ]
    block(msg)


if __name__ == "__main__":
    main()
