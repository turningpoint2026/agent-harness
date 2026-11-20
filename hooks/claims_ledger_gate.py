#!/usr/bin/env python3
"""Claims-ledger gate (2026-08-09). PreToolUse hook on Write / Edit / MultiEdit.

Enforces ALWAYS-APPLY rule 1's *Facts* bullet at write time: every factual
claim in a deliverable carries an inline source, or an explicit uncertainty
label. Until now rule 1 existed only as text, and text gets read and then
overridden under time pressure. This makes the cheap path the compliant one.

WHAT IT CHECKS (structural, deterministic, no LLM, no network)
    A claim-shaped LINE in a deliverable-shaped .md file under a project root
    must carry a source token on that line (or the line immediately after).
    Claim-shaped means one of:
      NUM     money, percentages, magnitudes (5.47M), or a "hard" number
              (>=100, or carrying a decimal / thousands comma)
      ATTRIB  a byline, "according to X", or Name + reporting verb
              (said / found / reported / authored / coauthored / approved ...)
      STATUS  process assertions: was sent / has been paid / is confirmed ...
    Source token means any of:
      (source: ...)          (verified via ...)      a bare filename.ext
      [UNVERIFIED: ...]      [INFERRED]             [NEEDS SOURCE]  [GAP: ...]
      a markdown link to a URL or path              file.md:123     [^fn]

WHAT IT CANNOT CHECK
    Whether the named source actually says the thing. That is semantic and
    not machine-checkable here; it is the job of /verify-claims, which runs
    an adversarial read-only subagent over the cited sources. This gate only
    guarantees that every claim NAMES something falsifiable. Deliberately so:
    a grep-based VERIFIED stamp would launder unverified claims behind a
    green check, which is worse than no gate at all
    (cf. memory: subagent-claims-are-not-verified).

SCOPE (kept narrow on purpose — a gate that misfires gets disabled)
    .md only; deliverable-shaped basename (report/results/summary/findings/
    memo/brief/analysis/audit/prelim/synthesis); >= MIN_CHARS of content;
    under a project root (a folder holding Claude_ProjectMemory_*.md, or any
    000PROJECT* ancestor; HARNESS_PROJECT_DIR_PREFIX changes the prefix).
    Long-form academic drafts are out of scope by design: "draft" is not a
    trigger word, because they cite through a bibliography and were governed
    by a separate prose hook (not in this repository).

    Only PROSE lines are scanned. Skipped: code fences, frontmatter, headings,
    blockquotes, ### >>> SUGGEST blocks, tables, and any line that is data
    rather than assertion (fewer than 6 words, more than 3 numeric tokens,
    numeric density over 30%, or fewer than 5 word-shaped tokens).

    That exclusion is calibrated, not guessed. Dry-run over 200 real
    deliverables in two research project folders, 2026-08-09:
    scanning every line flagged 194/200 files and 17% of all lines — regression
    coefficient rows, crosstabs, p-value legends. A gate at that rate gets
    switched off within a week, so numbers presented AS DATA are out of scope
    and only numbers embedded in claims about the world are gated. Tables
    therefore pass unchecked; /verify-claims is what covers them.

ESCAPES, in order of preference
    1. Cite the source inline.  2. Label it [UNVERIFIED: reason] / [INFERRED].
    3. User-only bypass: the user types "override claims gate" in a prompt
       (coverage_bypass_listener.py stamps the per-session flag).
    Blocks on EVERY attempt until one of those holds — no retry escape, since
    unlike the coverage gate the fix is mechanical and always available.

Honesty note: friction and legibility, not security. Claude can write the
bypass flag via Bash; doing so is a deliberate, conspicuous act in the
transcript rather than the lazy-path failure this exists to interrupt.

Origin: 2026-08-09 insights review. Across 84 sessions the dominant friction
class was confident assertion without verification - a fabricated
byline, an over-read ledger claiming a payment was received, an unsourced
rate comparison. Companion rule: CLAUDE_GLOBAL.md ALWAYS APPLY #1.
State: ~/.claude/hook_state/ (override for tests: CLAUDE_HOOK_STATE_DIR).
"""

import json
import os
import re
import sys

MIN_CHARS = 400
MAX_LISTED = 20
SNIPPET = 110

PROJECT_MEMORY_RE = re.compile(r"^Claude_ProjectMemory_.*\.md$", re.IGNORECASE)
# A folder whose name starts with this prefix is a project root (the owner's convention).
PROJECT_DIR_PREFIX = (os.environ.get("HARNESS_PROJECT_DIR_PREFIX") or "000PROJECT").upper()
DELIVERABLE_RE = re.compile(
    r"(report|results|summary|findings|synthesis|memo|brief|analysis"
    r"|audit|prelim|overview)",
    re.IGNORECASE,
)
# Internal scratch / infrastructure — never gated. Superset of the
# synthesis-gate list; a claims ledger about a report is not itself a report.
SKIP_NAME_RE = re.compile(
    r"(session_state|handoff|scope_creep|index|instructions|readme|corpus"
    r"|read_log|todo|proposal|template|checklist|_ledger|claims|log\b"
    r"|memory|notes_to_self|scratch|queue|manifest|_plan|plan_)",
    re.IGNORECASE,
)

# --- handoff STATUS claims (2026-09-06 harness redesign step 7, D11 = Y) ---
# A handoff / resume / session-state file is read by the NEXT session as fact,
# and its STATUS claims decay between the write and the read (two consecutive
# handoffs carried a PID "running" that a later query found dead).
# So under any registered project root (snapshot_roots.json) a
# handoff-shaped basename is gated for the STATUS class only, and the accepted
# proof is the check that produced the claim: "(checked HH:MM via <command>)".
# NUM and ATTRIB are not gated in handoffs (they are full of counts and names
# by design); the deliverable gate above is unchanged for every other file.
HANDOFF_NAME_RE = re.compile(r"(handoff|resume|session_state)", re.IGNORECASE)
CHECKED_TOKEN_RE = re.compile(r"\(\s*checked\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:Z|UTC|local)?\s+via\s+[^)]{2,}\)",
                              re.IGNORECASE)
# Claims a handoff makes about processes, files and lanes, beyond STATUS_RE.
HANDOFF_STATUS_RE = re.compile(
    r"\b(?:PID|process|session|lane|watchdog|campaign|orchestrator|worker|agent|task|front)\b"
    r"[^.\n]{0,60}?\b(?:is|are|was|were|still|remains?|stays?)\s+"
    r"(?:running|alive|live|active|dead|exited|idle|armed|disarmed|healthy|hung|stuck)\b"
    r"|\b(?:PID|pid)\s+\d{2,7}\s+(?:is\s+)?(?:running|alive|live|active|dead|exited)\b"
    r"|\b(?:is|are|was|were|has been|have been|had been)\s+"
    r"(?:running|alive|written|saved|committed|applied|banked|closed|parked|merged|pushed"
    r"|deployed|launched|relaunched|restarted|checkpointed|autosaved|backed\s+up|in\s+sync|clean)\b",
    re.IGNORECASE,
)
def snapshot_roots_path():
    """The project-root registry (JSON {"roots": [{"path": ...}]}): env override
    CLAUDE_SNAPSHOT_ROOTS, else snapshot_roots.json one folder above this file.
    The private harness shared this registry with an auto-snapshot tool."""
    env = os.environ.get("CLAUDE_SNAPSHOT_ROOTS") or os.environ.get("CLAUDE_SNAPSHOT_REGISTRY")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "snapshot_roots.json")


def snapshot_roots():
    try:
        with open(snapshot_roots_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        return [os.path.normcase(os.path.normpath(r["path"])) for r in d.get("roots", []) if r.get("path")]
    except Exception:
        return []


def under_snapshot_root(file_path):
    p = os.path.normcase(os.path.normpath(os.path.abspath(file_path)))
    for r in snapshot_roots():
        if p == r or p.startswith(r.rstrip("\\/") + os.sep):
            return True
    return False


def is_handoff(file_path):
    base = os.path.basename(file_path)
    return bool(HANDOFF_NAME_RE.search(base)) and under_snapshot_root(file_path)


# --- .tex support (2026-08-28 harness audit item 8) -------------------
# Direct .tex edits previously bypassed this gate entirely (the .md->pandoc
# path was covered, a project's paper.tex was not). Deliverable shape
# for .tex = DELIVERABLE_RE *or* a manuscript-ish name.
TEX_MAIN_RE = re.compile(
    r"(paper|main|manuscript|response|letter|appendix|intro|theory"
    r"|method|result|discussion|conclusion)",
    re.IGNORECASE,
)
_TEX_ENVS = (r"(table|tabular|longtable|figure|equation|align|eqnarray"
             r"|verbatim|filecontents|thebibliography|tikzpicture|matrix|array)")
TEX_BEGIN_RE = re.compile(r"\\begin\{" + _TEX_ENVS + r"\*?\}")
TEX_END_RE = re.compile(r"\\end\{" + _TEX_ENVS + r"\*?\}")
# In tex prose, a citation/footnote command IS the source token.
TEX_SOURCE_RE = re.compile(r"\\(cite\w*|footnote|bibitem|ref|autoref|cref)\b")

SOURCE_TOKEN_RE = re.compile(
    r"\(\s*sources?\s*[:=]"                      # (source: X)
    r"|^\s*sources?\s*:"                         # a bare "Source:" line
    r"|\(\s*verified\s+via"
    r"|\[\s*(?:UNVERIFIED|INFERRED|NEEDS\s+SOURCE|GAP|ESTIMATE|ASSUMPTION)"
    r"|\]\(\s*(?:https?://|\.{0,2}[/\\]|[A-Za-z]:[/\\]|#)"   # md link
    r"|\[\^[^\]]+\]"                                          # footnote ref
    r"|https?://"
    r"|\b[\w.@$-]+\.(?:md|csv|json|jsonl|py|txt|xlsx|xls|docx|doc|pdf|tex|db|sql|R)\b"
    r"|\b(?:msg|message|thread)\s*(?:id)?\s*[:#]?\s*[0-9a-f]{8,}"  # email ids
    # Page / line / endnote anchors — the owner's standard form when quoting
    # books and when auditing a draft against its own line numbers.
    r"|\bpp?\.\s*\d+"                            # p.81   pp.125-126
    r"|\[\s*kindle\s+p\.?\s*\d+"                 # [kindle p.204]
    r"|\blines?\s+\d+"                           # (line 94)
    r"|\^\d+\^"                                  # ^42^ endnote marker
    r"|\b(?:19|20)\d{2}[a-z]?\s*[:,]\s*\d+",     # Author et al. (1978: 387)
    re.IGNORECASE | re.MULTILINE,
)

# --- claim shapes -------------------------------------------------------
MONEY_RE = re.compile(r"[$€£¥]\s?\d")
PCT_RE = re.compile(r"\d\s?%|\bpercent\b|\bpercentage points?\b", re.IGNORECASE)
MAG_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s?(?:[KMB]|thousand|million|billion)\b")

# Numbers we do NOT treat as claims: structural references, dates, times,
# versions, and anything living inside a path or identifier.
SOFT_NUM_RE = re.compile(
    # Version triples first: "v2.1.0" must not decay to the decimal "1.0"
    # after the structural-reference branch eats the leading "v2".
    r"\bv?\d+\.\d+(?:\.\d+)+\b"
    r"|(?:\b(?:p{1,2}|pg|page|para|sec|section|§|step|stage|item|fig|figure"
    r"|table|tbl|chapter|ch|line|row|col|column|v|ver|version|round|phase"
    r"|note|footnote|rule|q|question|appendix|no|num|#)\.?\s*#?\s*\d+)"
    r"|\b\d{4}-\d{2}-\d{2}\b"                    # ISO date
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"         # slash date
    r"|\b\d{1,2}:\d{2}\b"                        # time
    r"|\b(?:19|20)\d{2}\b"                       # bare year
    r"|[\w./\\-]*\d[\w./\\-]*\.(?:md|csv|json|jsonl|py|txt|xlsx|docx|pdf|tex)",
    re.IGNORECASE,
)
HARD_NUM_RE = re.compile(r"\b\d[\d,]*\.\d+\b|\b\d{1,3}(?:,\d{3})+\b|\b\d{3,}\b")

NAME = r"[A-Z][A-Za-z'’-]+"
# Speech / authorship verbs only, and past tense only. Present-tense forms
# (says, notes, claims, reports, states, finds, shows) are noun-ambiguous and
# were the single largest false-positive source in the 2026-08-09 dry run:
# "Methodology notes", "Project claims", "Both reports", "Stata's reports".
# Participle-prone verbs (found, showed, estimated, concluded) are out too:
# "Fabrications found: 2" is not an attribution.
REPORT_VERB = (
    r"said|wrote|reported|argued|noted|stated|confirmed|authored|coauthored"
    r"|co-authored|claimed|emailed|told|recommended|approved|requested"
    r"|acknowledged|declined|testified|announced|admitted|denied"
)
# Capitalized tokens that are not proper nouns: document referents (a
# fact-check doc's "Source says:" IS the citation, not an unsourced claim),
# pronouns, and sentence-initial common nouns.
GENERIC_SUBJECTS = {
    "source", "sources", "chapter", "book", "paper", "text", "note", "notes",
    "description", "methodology", "doc", "docs", "pdf", "manuscript",
    "transcript", "article", "listing", "review", "reviews", "table",
    "figure", "appendix", "original", "quote", "summary", "report",
    "reports", "abstract", "title", "comment", "comments", "prompt",
    "instructions", "draft", "memo", "email", "thread", "message",
    "it", "this", "that", "these", "those", "they", "we", "he", "she",
    "there", "here", "the", "a", "an", "who", "which", "what",
    "both", "all", "each", "one", "two", "three", "some", "most", "many",
    "several", "none", "total", "overall", "key", "final", "first", "second",
    "result", "results", "data", "evidence", "analysis", "finding",
    "findings", "claim", "claims", "fabrication", "fabrications", "issue",
    "issues", "fix", "fixes", "error", "errors", "gap", "gaps",
    "agent", "worker", "step", "round", "phase", "stage", "verdict",
    "status", "rationale", "section", "line", "lines", "page", "pages",
    "author", "authors", "calculate", "confirm", "verify", "check",
}
ATTRIB_ACCORDING_RE = re.compile(r"\baccording\s+to\s+(" + NAME + r")")
ATTRIB_NAMEVERB_RE = re.compile(
    r"\b(" + NAME + r")(?:'s)?(?:\s+(?:et\s+al\.?|and\s+" + NAME
    + r"|" + NAME + r"))?\s+(?:" + REPORT_VERB + r")\b"
)
ATTRIB_AUTHORS_RE = re.compile(r"^\s*(?:authors?|byline)\s*[:=]\s*\S",
                               re.IGNORECASE | re.MULTILINE)
BYLINE_RE = re.compile(r"\b(\w+)\s+by\s+(" + NAME + r")\s+(" + NAME + r")\b")
BYLINE_START_RE = re.compile(r"^\s*by\s+(" + NAME + r")\s+(" + NAME + r")\b",
                             re.IGNORECASE | re.MULTILINE)
# "sorted by Best Seller", "ranked by Total Sales" — UI strings, not bylines.
NOT_BYLINE_VERBS = {
    "sorted", "ordered", "filtered", "ranked", "grouped", "divided",
    "multiplied", "followed", "weighted", "indexed", "measured", "scored",
    "split", "keyed", "joined", "matched", "replaced", "affected", "driven",
}


def _is_proper(tok):
    return tok.lower().rstrip("'s") not in GENERIC_SUBJECTS


def attrib_match(text):
    """A claim attributed to a named person, org or document author."""
    m = ATTRIB_ACCORDING_RE.search(text)
    if m and _is_proper(m.group(1)):
        return True
    for m in ATTRIB_NAMEVERB_RE.finditer(text):
        if _is_proper(m.group(1)):
            return True
    if ATTRIB_AUTHORS_RE.search(text):
        return True
    if BYLINE_START_RE.search(text):
        return True
    for m in BYLINE_RE.finditer(text):
        if m.group(1).lower() in NOT_BYLINE_VERBS:
            continue
        if _is_proper(m.group(2)) and _is_proper(m.group(3)):
            return True
    return False
STATUS_RE = re.compile(
    r"\b(?:was|were|has been|have been|had been|is|are)\s+"
    r"(?:sent|paid|filed|submitted|approved|rejected|accepted|confirmed"
    r"|signed|mailed|delivered|completed|scheduled|booked|ordered|received"
    r"|verified|published|cancelled|canceled|granted|denied|renewed|refunded)\b"
    r"|\b(?:sent|paid|filed|submitted|signed|mailed|ordered|booked|refunded)"
    r"\s+(?:on|to)\s+" + NAME,
    re.IGNORECASE,
)

KIND_LABEL = {"NUM": "number", "ATTRIB": "attribution", "STATUS": "status"}


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


def get_content(tool_input):
    content = tool_input.get("content") or tool_input.get("new_string") or ""
    if not content and isinstance(tool_input.get("edits"), list):
        content = "\n".join(
            e.get("new_string", "") for e in tool_input["edits"] if isinstance(e, dict)
        )
    return content


def find_project_root(file_path):
    d = os.path.dirname(os.path.abspath(file_path))
    for _ in range(10):
        try:
            names = os.listdir(d)
        except OSError:
            return None
        if any(PROJECT_MEMORY_RE.match(n) for n in names):
            return d
        if os.path.basename(d).upper().startswith(PROJECT_DIR_PREFIX):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def has_hard_number(text):
    """A number that asserts a quantity, after masking structural references."""
    if MONEY_RE.search(text) or PCT_RE.search(text) or MAG_RE.search(text):
        return True
    masked = SOFT_NUM_RE.sub(" ", text)
    return bool(HARD_NUM_RE.search(masked))


NUMERIC_TOK_RE = re.compile(r"^\W*[-+~<>=]?\d[\d,.%$*()\[\]{}<>=/:+-]*\W*$")
WORD_TOK_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{2,}")

MIN_WORDS = 5
MIN_WORD_TOKENS = 3
MAX_NUM_TOKENS = 3
MAX_NUM_DENSITY = 0.30


def is_prose(text):
    """True for assertion-shaped lines; False for data rows and fragments.

    The single most important filter in this hook. Regression tables, p-value
    legends and crosstabs are numbers presented as data, not claims about the
    world, and gating them buries the real catches.
    """
    if text.lstrip().startswith("|"):
        return False
    toks = text.split()
    if len(toks) < MIN_WORDS:
        return False
    nnum = sum(1 for t in toks if NUMERIC_TOK_RE.match(t))
    if nnum > MAX_NUM_TOKENS or nnum / len(toks) > MAX_NUM_DENSITY:
        return False
    return len(WORD_TOK_RE.findall(text)) >= MIN_WORD_TOKENS


def classify(text, handoff=False):
    if handoff:
        # STATUS only; a handoff's counts and names are its inventory, not claims.
        if STATUS_RE.search(text) or HANDOFF_STATUS_RE.search(text):
            return "STATUS"
        return None
    if attrib_match(text):
        return "ATTRIB"
    if STATUS_RE.search(text):
        return "STATUS"
    if has_hard_number(text):
        return "NUM"
    return None


def strip_markup(line):
    """Drop list bullets, emphasis and inline code so shapes match cleanly."""
    s = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)
    s = re.sub(r"`[^`]*`", " ", s)
    return s.replace("**", "").replace("__", "")


PROVENANCE_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\*{0,2}|#{1,6}\s*)"
    r"(?:sources?|data|inputs?|provenance|based\s+on|derived\s+from|computed\s+from)"
    r"\*{0,2}\s*[:=]?\s*\S",
    re.IGNORECASE,
)
PROVENANCE_SCAN_LINES = 20


def has_provenance_header(lines):
    """True if the document declares where its numbers come from, up top.

    A report computed from one dataset is honestly cited by one provenance
    line; demanding a per-sentence (source: ...) on thirty derived figures is
    the kind of friction that gets a gate disabled. Waives NUM only —
    attribution and status claims still need per-claim sourcing, because
    those are the classes that actually hallucinate
    (memory: process-claims-need-sources-like-numbers).
    """
    seen = 0
    for line in lines:
        if not line.strip():
            continue
        seen += 1
        if seen > PROVENANCE_SCAN_LINES:
            return False
        if PROVENANCE_RE.match(line) and SOURCE_TOKEN_RE.search(line):
            return True
    return False


def scan(content, is_tex=False, handoff=False):
    """Return [(lineno, kind, text)] for claim lines lacking a source token.
    handoff=True: STATUS class only, and "(checked HH:MM via <command>)" is a
    source token (step 7)."""
    lines = content.split("\n")
    waive_num = has_provenance_header(lines)
    n = len(lines)
    skip = [False] * n
    in_fence = in_suggest = False
    in_texenv = 0
    fence = None

    # frontmatter
    i = 0
    if lines and lines[0].strip() in ("---", "+++"):
        skip[0] = True
        for j in range(1, n):
            skip[j] = True
            if lines[j].strip() == lines[0].strip():
                break
        i = j + 1 if n > 1 else 1

    for idx in range(n):
        raw = lines[idx]
        s = raw.strip()
        if idx < i:
            skip[idx] = True
            continue
        m = re.match(r"^(```+|~~~+)", s)
        if m:
            if not in_fence:
                in_fence, fence = True, m.group(1)[:3]
            elif s.startswith(fence):
                in_fence = False
            skip[idx] = True
            continue
        if in_fence:
            skip[idx] = True
            continue
        if is_tex:
            b, e = TEX_BEGIN_RE.search(s), TEX_END_RE.search(s)
            if b and e:
                skip[idx] = True
                continue
            if b:
                in_texenv += 1
                skip[idx] = True
                continue
            if e:
                in_texenv = max(0, in_texenv - 1)
                skip[idx] = True
                continue
            if in_texenv or s.startswith("%") or s.startswith("\\"):
                skip[idx] = True
                continue
        if s.startswith("### >>>"):
            in_suggest = True
        if in_suggest:
            skip[idx] = True
            if s.startswith("### <<<"):
                in_suggest = False
            continue
        if not s or s.startswith("#") or s.startswith(">"):
            skip[idx] = True
            continue
        if re.match(r"^\s*(?:[-*_]\s*){3,}$", s):  # horizontal rule
            skip[idx] = True

    out = []
    for idx in range(n):
        if skip[idx]:
            continue
        raw = lines[idx]
        text = strip_markup(raw)
        if not is_prose(text):
            continue
        kind = classify(text, handoff=handoff)
        if not kind or (kind == "NUM" and waive_num):
            continue
        window = raw
        if idx + 1 < n:
            nxt = lines[idx + 1]
            if re.match(r"^\s*(?:\(?\s*sources?\s*[:=]|\[(?:UNVERIFIED|INFERRED"
                        r"|NEEDS|GAP|ESTIMATE|ASSUMPTION))", nxt, re.IGNORECASE) \
               or (nxt.startswith((" ", "\t")) and nxt.strip()):
                window = raw + "\n" + nxt
        if handoff and CHECKED_TOKEN_RE.search(window):
            continue
        if SOURCE_TOKEN_RE.search(window):
            continue
        if is_tex and TEX_SOURCE_RE.search(window):
            continue
        out.append((idx + 1, kind, text.strip()))
    return out


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
    is_tex = base.lower().endswith(".tex")
    if not (base.lower().endswith(".md") or is_tex):
        sys.exit(0)
    handoff = (not is_tex) and is_handoff(file_path)
    if not handoff:
        if SKIP_NAME_RE.search(base) or PROJECT_MEMORY_RE.match(base):
            sys.exit(0)
        if is_tex:
            if not (DELIVERABLE_RE.search(base) or TEX_MAIN_RE.search(base)):
                sys.exit(0)
        elif not DELIVERABLE_RE.search(base):
            sys.exit(0)

    content = get_content(tool_input)
    # _codex_after: the file a Codex patch produces (Codex integration Part 2).
    # A short hunk into a deliverable-sized file is still scanned, so Codex cannot
    # slip an unsourced claim past the size threshold one hunk at a time
    # (Codex review case claims_short_hunk_full_file, 2026-09-07).
    if len(content) < MIN_CHARS and len(tool_input.get("_codex_after") or "") < MIN_CHARS:
        sys.exit(0)

    if not handoff and find_project_root(file_path) is None:
        sys.exit(0)

    sid = payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "nosession"
    # Deliberately does NOT honor the coverage-gate bypass: they authorize
    # different things (corpus coverage vs per-claim sourcing).
    if os.path.isfile(os.path.join(state_dir(), f"claims_bypass_{safe(sid)}.flag")):
        sys.exit(0)

    violations = scan(content, is_tex=is_tex, handoff=handoff)
    if not violations:
        sys.exit(0)

    shown = violations[:MAX_LISTED]
    if handoff:
        msg = [
            f"[claims-gate] BLOCKED: handoff {base} carries {len(violations)} STATUS claim(s) "
            f"with no check named and no uncertainty label.",
            "The next session reads a handoff as fact, and status decays between the write "
            "and the read (a PID recorded as running was dead at the next read).",
            "",
        ]
    else:
        msg = [
            f"[claims-gate] BLOCKED: {base} contains {len(violations)} claim(s) "
            f"with no source and no uncertainty label.",
            "CLAUDE_GLOBAL.md ALWAYS APPLY #1, Facts bullet: if a reader could "
            "look it up, it needs a source.",
            "",
        ]
    for ln, kind, text in shown:
        t = text if len(text) <= SNIPPET else text[:SNIPPET].rstrip() + "..."
        msg.append(f"  line {ln:>4}  [{KIND_LABEL[kind]}]  {t}")
    if len(violations) > MAX_LISTED:
        msg.append(f"  ... and {len(violations) - MAX_LISTED} more")
    if handoff:
        msg += [
            "",
            "Fix each one: name the check that produced the claim, in the same line ->",
            "  (checked 14:02 via tasklist | findstr 32012)   (checked 14:05 via Get-Content journal.log -Tail 3)",
            "or label it [UNVERIFIED: not re-checked since HH:MM]. /resume re-runs every named check "
            "before trusting the line; a line with no check is flagged, not carried forward.",
            "Blocks on every attempt. Bypass is USER-ONLY: the user types \"override claims gate\".",
        ]
        block(msg)
    msg += [
        "",
        "Fix each one, cheapest first:",
        "  1. Cite it inline    -> (source: file.csv)  (source: SEC 10-K, "
        "verified via WebFetch)  [foo](https://...)  notes.md:412  p.81",
        "  2. Label the doubt   -> [UNVERIFIED: reason]  [INFERRED]  "
        "[NEEDS SOURCE]  [GAP: ...]",
        "  3. Cut the claim if neither is true.",
        "",]
    if any(k == "NUM" for _, k, _ in violations):
        msg += [
            "For a report whose figures all come from one dataset, a single "
            "provenance line in the first 20 lines covers every [number] "
            "flag at once, e.g.:",
            "    Source: panel_build.csv (2026-08-02 build)",
            "It does NOT cover [attribution] or [status] flags. Those are "
            "the classes that actually hallucinate, so they stay per-claim.",
            "",
        ]
    msg += [
        "This gate only checks that a source is NAMED, not that it says what "
        "you claim. Run /verify-claims for the semantic pass before this goes "
        "to a reader.",
        "It blocks on every attempt. Do not route around it via Bash/Python. "
        "Bypass is USER-ONLY: the user types \"override claims gate\".",
    ]
    block(msg)


if __name__ == "__main__":
    main()
