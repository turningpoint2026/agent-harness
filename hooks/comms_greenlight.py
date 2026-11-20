#!/usr/bin/env python3
"""Comms greenlight stamp + pipeline reminder.

UserPromptSubmit hook. Two jobs:

1. STAMP: if the user's own prompt contains an explicit greenlight phrase
   ("go ahead", "send it", "greenlight", "GO EMAIL", "create the draft",
   Chinese fa-ba), write a sentinel file that external_comms_gate.py
   accepts for 30 minutes within the same session. Only THIS hook can
   stamp.

   CORRECTED 2026-08-10 -- the original docstring claimed this hook "only
   ever sees real user prompts, so Claude cannot fake one, which is what
   makes the gate hard enforcement". THAT WAS FALSE. Background task
   completions are delivered through UserPromptSubmit as well, and their
   payload is agent-authored text. On 2026-08-09 a workflow's own result
   summary contained the words "I reproduced the green light read-only:"
   -- describing a script's output -- and this hook matched "green light",
   stamped a live 30-minute external-comms approval, and announced it as
   "Matched user phrase". Nothing was sent, but the gate standing between
   subagent output and the owner's email and calendar had been opened by
   subagent output. A gate an agent can trip by writing a sentence is not
   a gate. MACHINE_ORIGIN_RE below refuses to stamp anything carrying a
   task-notification, system-reminder or slash-command-output marker.

2. REMIND: if the prompt talks about emailing/messaging a coauthor but
   contains no greenlight, inject the work-before-comms pipeline reminder
   (origin: 2026-07-27 -- an agent repackaged existing files and jumped
   straight to drafting a collaborator email; recurring failure).

Negation guard: a greenlight phrase preceded within 60 chars by don't /
not yet / hold off / wait etc. does NOT stamp ("don't send it yet").

Output goes to stdout (injected as context). Exit 0 always; any
unexpected error also exits 0 -- never wedge prompt submission.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime

STATE_DIR = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "state")
SENTINEL = os.path.join(STATE_DIR, "comms_greenlight.json")

# TIER 1 -- SELF-CONTAINED. The phrase already names the outbound act, so it
# stamps on its own. Deliberately NOT matched: planning statements like
# "we need to send him an update" -- intent is not approval.
SELF_CONTAINED_PATTERNS = [
    (r"\bsend it\b", re.I),
    (r"\bsend (them|him|her|those|these|the (email|message|draft|reply|invite))\b", re.I),
    (r"\bapproved?\b[^.!?\n]{0,40}\b(send|draft|email|message|invite)\b", re.I),
    (r"\b(send|draft|email)\b[^.!?\n]{0,20}\bapproved\b", re.I),
    (r"\byes,?\s+(send|create|save)\b", re.I),
    (r"\b(create|save|make)\s+(the\s+|a\s+)?(gmail\s+)?draft\b", re.I),
    (r"\bGO\s+(EMAIL|COMMS|SEND|SHARE)\b", 0),  # uppercase-only fallback token; SHARE added 2026-10-01 for shell writes into shares
    (r"\b(create|add)\s+(the\s+|those\s+|these\s+)?(calendar\s+)?(events?|invites?)\b", re.I),
    (r"发(吧|了吧|出去)", 0),
]

# TIER 2 -- QUALIFIED. Bare authorization words. These approve *something*, but
# say nothing about what, so on their own they are not approval to communicate.
# Each must sit within SEND_WINDOW chars of an explicit send act (SEND_ACT_RE)
# or it does not stamp.
#
# ADDED 2026-08-16, at the owner's instruction ("fix the greenlight matcher to
# require an explicit send phrase"), after two misfires in one planning session.
# The old bare-noun pattern r"\bgreen\s*-?\s*light\b" matched the word wherever
# it appeared: "二 greenlight counts as a cycle ship -- your call" (a question
# about a deliverable-accounting rule) stamped a live 30-minute approval, and so
# did the very message asking for this fix. Neither authorized any send.
QUALIFIED_PATTERNS = [
    (r"\bgreen\s*-?\s*ligh(t|ted|ting)\b", re.I),
    (r"\bgreen\s*-?\s*lit\b", re.I),
    (r"\bgo ahead\b", re.I),
]

SEND_WINDOW = 60

SEND_ACT_RE = re.compile(
    r"\b(send|sends|sending|sent|email|e-mail|emailing|mail|draft|drafting|"
    r"reply|replying|respond|responding|message|messaging|forward|post|"
    r"submit|submitting|invite|rsvp)\b|发送|回复|寄出",
    re.I,
)

# Talking ABOUT the gate is not using it. A prompt discussing the matcher, the
# hook, the sentinel or a misfire never stamps, whatever words it contains.
# Same origin as QUALIFIED_PATTERNS above: the request to harden this hook tripped it.
META_RE = re.compile(
    r"\b(matcher|regex|pattern|hook|hooks|sentinel|misfire[ds]?|false[ -]positive|"
    r"comms[_ -]?greenlight|external[_ -]?comms[_ -]?gate|the gate|this gate|"
    r"settings\.json|UserPromptSubmit)\b",
    re.I,
)

NEGATION_RE = re.compile(
    r"\b(don'?t|do not|not yet|never|hold off|wait|no need|without|stop|"
    r"instead of|rather than|before (you|we|i))\b|别|不要|先不",
    re.I,
)

INTENT_RE = re.compile(r"\b(email|send|draft|message|reply|update|submit)\b", re.I)

# Deliberation, not instruction. Checked in the same pre-match window as
# NEGATION_RE: "we need to send him an update", "I will send it myself",
# "maybe send them the draft" are all statements ABOUT a future send, and a
# future send is exactly what this gate is supposed to stand in front of.
# Added 2026-08-16 alongside the send-phrase requirement.
PLANNING_RE = re.compile(
    r"\b(need(s)? to|have to|has to|had to|should|shall|ought to|"
    r"want(s)? to|going to|gonna|plan(s|ning)? to|intend(s)? to|"
    r"i'?ll|we'?ll|i will|we will|would|could|might|maybe|perhaps|"
    r"eventually|at some point|later|tomorrow|next week)\b",
    re.I,
)

# Prompts that did NOT come from the user's keyboard. Background task completions,
# system reminders and slash-command output all arrive through UserPromptSubmit
# carrying text that Claude or a subagent wrote. Approval must never be read out
# of any of them. Added 2026-08-10 after a workflow result summary containing the
# phrase "green light" stamped a live external-comms greenlight.
MACHINE_ORIGIN_RE = re.compile(
    r"""(
        <task-notification> | </task-notification> | <task-id> |
        \[SYSTEM\s+NOTIFICATION\s*-\s*NOT\s+USER\s+INPUT\] |
        <system-reminder> | <local-command-stdout> | <command-name> |
        This\s+is\s+an\s+automated\s+background-task\s+event |
        Per-agent\s+results: | <diagnostics> | <usage><agent_count>
    )""",
    re.I | re.X,
)
# Who counts as an outside recipient for the reminder: role words, plus any names listed
# (comma-separated) in HARNESS_CONTACT_NAMES.
_CONTACTS = [re.escape(n.strip()) for n in os.environ.get("HARNESS_CONTACT_NAMES", "").split(",") if n.strip()]
PEOPLE_RE = re.compile(
    r"\b(" + "|".join(_CONTACTS + ["coauthor", "co-author", "collaborator", "editor", "reviewer",
                                   "organizer", "client"]) + r")\b",
    re.I,
)

REMINDER = (
    "[comms-gate] External-communication pipeline reminder: the message/email "
    "is the LAST step. Order: (1) do the substantive work, (2) show it to "
    "the user and iterate, (3) the user types an explicit greenlight ('go ahead', "
    "'send it', 'GO EMAIL'), (4) only then draft/create/send. Drafting the "
    "outbound text before steps 1-3 is the recurring failure this gate exists "
    "for (memory: work-before-external-comms, 2026-07-27)."
)


def main() -> None:
    data = json.loads(sys.stdin.read() or "{}")
    prompt = data.get("prompt") or ""
    session = data.get("session_id") or ""
    if not prompt:
        return

    # Machine-origin prompt: never stamp, never remind. Approval is something
    # the user types; it is not something that can appear inside a tool result.
    if MACHINE_ORIGIN_RE.search(prompt):
        return

    # Discussing the gate is not opening it.
    if META_RE.search(prompt):
        if INTENT_RE.search(prompt) and PEOPLE_RE.search(prompt):
            print(REMINDER)
        return

    candidates = [(p, f, False) for p, f in SELF_CONTAINED_PATTERNS]
    candidates += [(p, f, True) for p, f in QUALIFIED_PATTERNS]

    for pattern, flags, needs_send_act in candidates:
        m = re.search(pattern, prompt, flags)
        if not m:
            continue
        window = prompt[max(0, m.start() - 60): m.start()]
        if NEGATION_RE.search(window) or PLANNING_RE.search(window):
            continue
        # Bare authorization word: require an explicit send act beside it.
        if needs_send_act:
            near = prompt[max(0, m.start() - SEND_WINDOW): m.end() + SEND_WINDOW]
            if not SEND_ACT_RE.search(near):
                continue
        os.makedirs(STATE_DIR, exist_ok=True)
        snippet = prompt[max(0, m.start() - 40): m.end() + 40].strip()
        payload = {
            "stamped_at_epoch": time.time(),
            "stamped_at": datetime.now().isoformat(timespec="seconds"),
            "session_id": session,
            "user_quote": snippet,
        }
        with open(SENTINEL, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print(
            "[comms-gate] Greenlight stamped (valid 30 min, this session only). "
            "Matched user phrase: \"" + snippet + "\". External-comms tools are "
            "now unblocked for the approved action -- do only what was approved."
        )
        return

    if INTENT_RE.search(prompt) and PEOPLE_RE.search(prompt):
        print(REMINDER)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
