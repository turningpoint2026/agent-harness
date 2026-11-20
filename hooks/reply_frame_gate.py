#!/usr/bin/env python3
r"""Reply-frame gate v3: a reply that hands the user an action carries an orange line.

Stop hook. Reply contract v3 (owner ruling 2026-10-30; the always-loaded rule
file carries a situation table): silence means nothing needs the user. A
routine turn is one line; a decision, an action the user must take, a
blocker, a finished scope and an orientation turn each have one fixed form.
The gate checks content only, never the form of the four labels:

  G1 BOUNCE (the only bounce): the final hands the user an action (resume,
     paste, launch, approve, "needs you: <x>", "you need to", "if you want")
     and no line starts with the orange 🟦 or the 🟧 decision marker (or is a
     diff '! ' line whose first word is You). One more line fixes it, so the
     bounce asks for that one line only: '! 🟦 You <the action>' inside a
     diff fence, or '🟨 <result>' when the phrase is not the user's action.
     Round 2 (red team 2026-10-30, P1/P2/P5): besides HANDBACK_RE, the prose
     (fences blanked) is read for an imperative to the user at a sentence
     start (imper), a question asking for a go or a choice (ask), hand-back
     phrases ("your call", "on your go", "let me know") and a
     sentence-initial "Recommend" (rec); a negated session clause ("no
     successor is needed") is not a hand-back; a 🟦/🟧 mentioned mid-sentence
     no longer counts. A first hit on rec asks for the '- 🟧 Decision needed'
     line instead.
     Round 3 (2026-10-31): a phrase or imper hit whose own clause has a
     negator before it ("Nothing is waiting on you") is not a hand-back.
     Focus view (Claude only; settings viewMode "focus", read from
     CLAUDE_SETTINGS_PATH or ~/.claude/settings.json, failing open) shows
     only the final message, so there the bounce asks for the whole reply
     again with the line added first, or unchanged if the hit is false.
  G2 NOTE: over 1,200 characters of the reply's own text (quoted code fences
     dropped, link targets stripped) with no 🟧 and no 🟥, on a turn that is
     not an orientation turn (orientation = Finding / For / Big picture /
     Next all present), unless the prompt asked for detail (DETAIL_RE).
  G3 NOTE: banned boilerplate ("nothing needs you", "Big picture: unchanged",
     "Next: unchanged"); absence is the message.
  G4 NOTE: a turn the user did not start (peer message, task notification)
     over 4 lines or 700 characters, or 12 lines / 2,500 characters when it
     carries 🟧 or 🟥.
A note is a miss already on the user's screen (a bounce for it manufactures
repetition); it is written to state and a prompt hook injects it once at the
next prompt (pop_notes()).

History. v1 (2026-10-02): four labels open every reply, after the owner
reported constantly having to ask "so what is the finding here". v2
(2026-10-08): five fixed color rules, bounce versus note. v3 drops every
form check (labels, order, Finding signal, Big picture and Next forms,
repeats, fences, internal codes, previous-reply similarity, first-reply
rules) after the four-line default itself became boilerplate the user had
to read past, and a false "Next: nothing needs you" hid seven successor
launches in Codex campaign handoffs between 10/27 and 10/30 (replay over
saved replies).

A retry after a G1 bounce is graded by re-running G1 on the bounced reply
with the retry appended (log kinds retry-fixed / retry-still-wrong; after a
rec bounce, fixed means a 🟧 line at a line start); a retry that restates
the reply instead of adding one line logs retry-restated and leaves a note;
a retry of exactly one 🟨 line logs retry-declined (a measured false bounce)
and leaves no note. In focus view the retry is graded alone (the user sees
only it): unchanged from the bounced reply, or a lone 🟨 line, logs
retry-declined; still handing back with no orange line logs
retry-still-wrong; otherwise retry-fixed; retry-restated is never logged.

Bounce delivery (2026-10-31): hook_dispatch.py returns a Claude Stop bounce
as hookSpecificOutput.additionalContext (non-error hook feedback, so no hook
error reaches the user's screen). The transcript then carries an attachment
entry of type hook_additional_context (stop_feedback_text) instead of the
older "Stop hook feedback" user entry; parse_turn reads both. The bounced
reply is also kept in state for Claude, so a missed parse still grades the
retry.

Bounces once per prompt: this hook's own "Stop hook feedback" entries since
the prompt, and a state file keyed on the prompt (Codex uses its documented
stable turn_id). stop_hook_active silences the gate unless another hook's
bounce, not this one's, caused the continuation; then G1 reads the reply
above that bounce together with the continuation (the user sees both).

Not checked (config reply_frame_gate.json beside this file):
  * autopilot sessions: G1 bounces when "autopilot_g1_bounce" is true (the
    default since 2026-10-30: every "resume the handoff" miss in the replay
    was an autopilot reply); notes (G2 to G4) stay shadow-logged unless
    "autopilot_bounce" is true; both false = shadow log only;
  * headless runs (transcript entrypoint "sdk-cli"), unless
    "exempt_headless" is false;
  * a prompt that asks for a bare answer ("just the number", "output only
    that quoted bullet") when the reply is 300 characters or less;
  * turns with no text;
  * Codex realtime voice items on the spoken channel (the final opens with
    [COMPLETE] or [STATUS]): the host's realtime contract makes them context
    for the voice model, spoken instead of displayed. An inline item
    (::codex-realtime-inline{}) is displayed Markdown and is checked with
    the directive line stripped.
2026-10-13 fixes kept: hook stdin is read as UTF-8 bytes whatever the code
page (a cp1252 decode turned every emoji into mojibake: 31 Codex rows, 0
passes); the bounced Codex reply is kept in state per prompt key so the
retry is graded merged.
Every decision is logged to ~/.claude/state/reply_frame_gate_log.csv
(kind = pass / bounce / note / shadow / retry-fixed / retry-still-wrong /
retry-restated / retry-declined / exempt; why = "v3" on a pass).

HARNESS_OWNER_NAME (env, optional) names the person the agent hands actions
to; the default is "the user".

Fails open: any unexpected error exits 0.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("CLAUDE_HOOK_STATE_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "state")
LOG_PATH = os.path.join(STATE_DIR, "reply_frame_gate_log.csv")
CONFIG_PATH = os.environ.get("CLAUDE_REPLY_FRAME_CONFIG") or os.path.join(HERE, "reply_frame_gate.json")
DEFAULT_CONFIG = {"enabled": True, "autopilot_bounce": False, "autopilot_g1_bounce": True, "exempt_headless": True}
SELF_TAG = "reply_frame_gate.py"
VERSION = 3
# Who the agent hands actions to; the bounce text and one phrase layer name this person or role.
OWNER = (os.environ.get("HARNESS_OWNER_NAME") or "the user").strip()
_OWNER_RE = r"(?:the user|" + re.escape(OWNER) + r")"

sys.path.insert(0, HERE)
try:
    from session_mode import effective_mode
except Exception:  # pragma: no cover
    def effective_mode(sid, transcript_path=None):
        return "default", None, None

LABELS = ["Finding", "For", "Big picture", "Next"]
LABEL_RE = re.compile(
    r"^\s*(?:(?:[-*>!]\s+)|\[|#{1,6}\s+)?(?:\*\*|__)?\s*"
    r"(Finding|For\b(?!\s+(?:example|instance)\b)[^:\n*]{0,60}|Big picture|Next(?:\s+steps?)?)"
    r"\s*(?:\*\*|__)?\s*:", re.I)
MACHINE_PROMPT = ("PEER_MESSAGE", "<task-notification", "Another Claude session sent a message",
                  "[PEER_MESSAGE", "<cross-session-message")
# Claude delivers a message from another session as an isMeta user entry
# ("Another Claude session sent a message: <cross-session-message ...>"); until
# 2026-10-08 the gate skipped every isMeta entry, so the reply to a peer
# message was glued onto the previous turn and never checked.
PEER_META = ("Another Claude session sent a message", "<cross-session-message")
NOT_A_PROMPT = ("Stop hook feedback", "<local-command", "<command-name>", "<command-message>",
                "Caveat:", "[Request interrupted")
# the user's explicit request about the reply's FORM. A scope instruction ("fix the typo and nothing else",
# "change only line 3") is not a form request (red team n4, B01, B03, B04).
BARE_RE = re.compile(r"\bjust\s+(?:give me\s+)?the\s+(?:number|answer|word|count|value|name|path|list|command|figure|total)\b|"
                     r"\bonly\s+the\s+(?:number|word|count|value|name|path|figure|total)\b(?!\s+(?:matters|of|in|on))|"
                     r"\b(?:yes or no|yes/no)(?:\s+only)?\s*[.?!]?\s*$|\bone[- ]word answer\b|\bno frame\b|"
                     r"\bwithout the four lines\b|"
                     # 2026-10-13: "Output only that quoted bullet" (a Codex session). Only a named output FORM
                     # counts; "output only confirmed issues" limits content (Codex review)
                     r"\b(?:output|print|return|reply with|respond with|answer with)\s+only\s+(?:(?:the|that|this|a|an|one)\s+)?"
                     r"(?:(?:quoted|exact|verbatim|single|bare|final|raw)\s+)?"
                     r"(?:bullet|line|sentence|quote|text|answer|number|value|word|name|path|command|json|result)s?\b", re.I)
REALTIME_SPOKEN_RE = re.compile(r"^\s*\[(?:COMPLETE|STATUS)\]")
REALTIME_INLINE_RE = re.compile(r"^\s*::codex-realtime-inline\{\}[ \t]*\r?\n?")

SYM_COMPLETE, SYM_DECISION, SYM_BLOCKED, SYM_ATTENTION, SYM_PARKED = "✅", "\U0001f7e7", "\U0001f7e5", "\U0001f7e8", "⬜"
SYM_NEXT, SYM_BIG = "\U0001f7e6", "\U0001f7ea"
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(\w*)\s*$")

# --- G1: a hand-back to the user ----------------------------------------------
# Run case-insensitively over the reply's whole text, code fences included (a
# handoff prompt to paste usually sits in a fence). Tuned on the 10/02-10/30
# replay (REPLAY_V3.md); each alternative carries its own word boundaries
# (register 2026-09-06: a trailing \b after a space or an emoji never matches).
HANDBACK_PARTS = [
    r"needs you: (?!none\b|nothing\b)",
    # the agent's own step ("I open the page", "I'll open it", "the command to open it", "and open it for
    # you", a link label "[Open the report](...)") is not a hand-back (replay round 1: 27 of 53 hits)
    r"(?<!\bI )(?<!I'll )(?<!\bto )(?<!\band )(?<!\bthen )(?<!\bwould )(?<!\bwill )(?<!\bBoth )(?<!\[)(?<!before you )"
    r"(?<!\bthe )"
    r"\b(?:resume|relaunch|re-launch|launch|paste|open) (?:this|the|that|it|a new|a fresh)\b(?![^.\n|;]{0,30}\bfor you\b)",
    r"\bin a (?:fresh|new) session\b",
    # the campaign handoffs of 10/27-10/30: "Next: nothing needs you; the successor starts with packet 11",
    # "... the [handoff](...) preserves the exact continuation" (a session he must launch)
    r"\bsuccessor (?:session|pickup|continues|starts|resumes|picks up|takes)\b",
    # ... unless the line says the launch is automatic ("the watchdog relaunches a fresh session")
    r"\bnothing (?:else )?needs you\b(?![^\n]*\b(?:watchdog|automatically|auto-?launch\w*)\b)"
    r"[^\n]{0,60}?\b(?:successor|hand-?off|(?:fresh|new|next) session)\b",
    r"\b(?:a|the) (?:fresh|new|next) session (?:continues|starts|resumes|picks up|takes|rewrites|can resume)\b",
    # round 1: bare "you could/can now/should/must" hit advice and quotes ("you could not see it",
    # "you have to work the screen out"); only an action verb after it is a hand-back
    r"\byou (?:must|need to|should|will need to|have to)(?: now| first| still)? (?:approve|confirm|decide|paste|resume|"
    r"relaunch|launch|open|start|restart|run|send|review|type|say|reply|check|reload|sign|log in|download|install|"
    r"pick|choose|rule)\b",
    r"\bplease\b",
    r"\b(?:your|his) (?:approval|ruling|go-ahead|greenlight|decision) (?:is|remains|still) (?:needed|required|open|pending)\b",
    r"\bawait(?:s|ing)? (?:your|his) (?:ruling|approval|decision|go)\b",
    r"\b(?:if|when|once) you (?:want|say|approve|confirm|decide)\b",
    # round 1: "I sent the command to open it; confirm you see it" (he must look and confirm)
    r"\b(?:confirm|check)(?: that)? (?:you (?:can )?see\b|(?:it|they|the (?:window|page|tab|folder|file))"
    r"(?:'s| is| are)? (?:appeared|showing|there|open|on your screen)\b)",
]
HANDBACK_RE = re.compile("|".join("(?:%s)" % p for p in HANDBACK_PARTS), re.I)
# text that is not the agent's own ask: a quoted span (a quoted rule, an email line, "please use this", on-screen
# text a video worker transcribed as 'please go to'). A single quote opens a span only when no letter precedes it,
# so an apostrophe (can't, user's) never does.
QUOTED_RE = re.compile(r"\"[^\"\n]{1,300}\"|“[^”\n]{1,300}”|‘[^’\n]{1,300}’|(?<![\w'])'[^'\n]{1,300}'(?![\w'])")
# the 'next:' field of an autopilot status line is the agent's own step ("next: check and open the page")
STATUS_NEXT_RE = re.compile(r"(?<=\|)[ \t]*next:[^|\n]*", re.I)
ORANGE_LINE_RE = re.compile(r"^\s*!\s.*\byou\b", re.I)

# --- G1 layers of round 2 (red team 2026-10-30, P1/P2; red team report section 4) -------------------
# They read the prose only (prose_text(): scan_text() plus fenced blocks blanked), so a quoted command or a
# pasted handoff prompt does not count; HANDBACK_RE above still reads the fences.
# imper: an imperative addressed to the user at a sentence start or after a hand-off label ("Next: start a fresh
# session ..."). The verb must take an object (determiner, file, backtick) so nouns ("Run 3 failed", "Open
# questions", "Check 12 PASS", "Copy of X saved") do not count; a trailing "for you" is the agent's own step.
_OBJ = (r"(?:(?:the|this|that|these|those|it|them|a|an|your|my|its|HANDOFF\w*)\b|[`*\[]|"
        r"\S+\.(?:md|txt|csv|html|py|ps1|json|docx|pdf|bat)\b)(?![^.\n|;]{0,30}\bfor you\b)")
_LABEL = r"(?:next(?: steps?)?|action|to continue|your (?:turn|move)|over to you|for you|then|tomorrow)"
# sentence start: line start, after . ; ! ?, after a bullet, or after a label; a '**Me:**'-style label exempts
_SENT = (r"(?:^|(?<=[.;!?])[ \t]+|^[ \t]*(?:[-*>]|\d+\.)[ \t]+|\b" + _LABEL + r"\s*(?:\*\*|__)?\s*[:,]\s*(?:\*\*|__)?[ \t]*)"
         r"(?!\*\*(?:me|i|agent|worker[^*]*)\b)(?:\*\*[^*\n]{1,30}\*\*[ \t]*)?")
IMPER_RE = re.compile(
    _SENT + r"(?:(?:then|now|tomorrow|tonight|next|once ready|when ready|whenever you(?:'re| are) ready),?[ \t]+)?"
    r"(?:start|kick off|fire up|spin up|bring up|open|launch|relaunch|re-launch|resume|paste|copy|run|re-run|rerun|restart|"
    r"reload|refresh|approve|confirm|verify|sign|send|mail|email|forward|drag|drop|move|upload|download|install|click|"
    r"double-click|log in(?:to)?|log into|sign in(?:to)?|point|feed|hand|review|trust|accept|reject)\b[ \t]+" + _OBJ, re.I | re.M)
# ask: a question to the user (a choice or a go)
ASK_RE = re.compile(
    r"(?:^|(?<=[.;:!?])[ \t]+|^[ \t]*[-*>][ \t]+)(?:want me to|shall i|should i|do you want|would you like|any objection|"
    r"ok(?:ay)? to|can i go ahead|may i|go ahead and|which (?:one )?(?:do|would|should) you|do i)\b[^?\n]{0,200}\?", re.I | re.M)
# phrase: hand-back phrases with no imperative verb
PHRASE_RE = re.compile(
    r"(?<!needs )(?<!need )\byour call\b|\bup to you\b|\bover to you\b|"
    r"\b(?:upload|send|launch|call|decision|choice|move|turn|pick|review|sign-off|approval|login|next step)\b[^.\n]{0,30}\b(?:is|stays|remains) yours\b|"
    r"\byours to (?:start|launch|run|send|decide|make|resume)\b|"
    r"\bwait(?:s|ing)? (?:on|for) (?:you|your)\b(?! next (?:task|instruction|message|prompt))|\bon your (?:word|go|say-so|signal|ok|okay|nod)\b|"
    r"\bwhen you give the word\b|\bif you(?:'d| would) like\b|\bif you prefer\b|"
    r"\b(?:say|reply|type|answer)[ \t]+(?:go|apply|yes|the word)\b|\blet me know\b|(?<!will )(?<!to )\btell me (?:whether|which|if)\b|"
    r"\b(?:needs|need|pending|awaiting|waiting on|blocked on) (?:your|his) (?:ok|okay|go|go-ahead|sign-off|input|approval|decision|ruling|choice|login|word)\b|"
    r"\bsign-off from you\b|"
    r"\b" + _OWNER_RE + r" (?:launches|resumes|starts|approves|sends|pastes|decides|picks|opens|confirms|reviews|uploads|signs)\b|"
    r"\bneeds you\b(?:\*\*|__)?[ \t]*(?:[:\-—–]|\*\*)+[ \t]*(?:\*\*)?[ \t]*(?!none\b|nothing\b)\w", re.I | re.M)
# rec (P1): a recommendation between the user's options with no decision line is a decision left in a routine line
# (Codex L1 t6, L1_off t6, L4 t8: "🟨 Recommend CSV ...", "🟨 I recommend keeping ...")
REC_RE = re.compile(r"(?:^|(?<=[.;:])[ \t]+|" + SYM_ATTENTION + r"[ \t]*|^[ \t]*[-*>][ \t]+)(?:\*\*)?(?:I )?recommend(?:ation)?\b(?:\*\*)?",
                    re.I | re.M)
PROSE_LAYERS = (("imper", IMPER_RE), ("ask", ASK_RE), ("phrase", PHRASE_RE), ("rec", REC_RE))
# neg: a session or successor match whose own clause is negated ("no successor session is needed") is not a
# hand-back (live false bounces Claude a2 t3, a3 t3). HANDBACK_PARTS[2:6] are the session/successor parts.
SESSION_PART_RE = re.compile("|".join("(?:%s)" % p for p in HANDBACK_PARTS[2:6]), re.I)
NEGATOR_RE = re.compile(r"\b(?:no|not|never|none|without|neither)\b|n't\b", re.I)
# round 3: the phrase and imper layers skip a hit whose clause has a negator before it (_negated_before)
NEGATABLE_LAYERS = ("phrase", "imper")
NEGATOR_BEFORE_RE = re.compile(r"\b(?:no|not|nothing|nobody|none|never|without)\b|n't\b", re.I)
CONJ_RE = re.compile(r"\b(?:and|but|until|unless|once|when|after|then|so|or)\b", re.I)
# orange: 🟦/🟧 count only where they start a line (after an optional '! ', '- ', '> ' or a label), and a diff
# '! ' line counts only when its first word is You; a mentioned marker or '! Thank you' no longer silences G1
_LINE_HEAD = r"^[ \t]*(?:[!>*-][ \t]*)*(?:\*\*)?[ \t]*(?:[A-Z][\w ]{0,20}:[ \t]*)?"
ORANGE_START_RE = re.compile(_LINE_HEAD + r"(?:" + SYM_NEXT + "|" + SYM_DECISION + ")", re.M)
DECISION_START_RE = re.compile(_LINE_HEAD + SYM_DECISION, re.M)
ORANGE_YOU_RE = re.compile(r"^\s*![ \t]+(?:" + SYM_NEXT + r"[ \t]*)?you\b", re.I)
ROUTINE_LINE_RE = re.compile(r"^[ \t]*(?:[>*-][ \t]*)*" + SYM_ATTENTION)

# --- notes ------------------------------------------------------------------
LONG_CAP = 1200                                          # G2
MACHINE_CAP = {"quiet": (4, 700), "loud": (12, 2500)}    # G4: content lines, chars
BANNED_RE = re.compile(                                  # G3
    r"\bnothing (?:else )?(?:more )?needs you\b|"
    r"\bbig picture\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(?:" + SYM_BIG + r"\s*)?unchanged\b|"
    r"\bnext\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(?:" + SYM_NEXT + r"\s*)?unchanged\b", re.I)
LINK_TARGET_RE = re.compile(r"\]\([^)\s]*\)|https?://\S+|<https?://[^>]*>")
# P6: G2 is skipped when the prompt asked for detail (red team case g1 on both hosts: 2 of 3 G2 notes were false)
DETAIL_RE = re.compile(r"\b(?:full detail|in detail|walk (?:me )?through|every (?:setting|step|item|row)|explain everything|"
                       r"all the details?)\b", re.I)

# P5: the G1 text offers an exit for a false hit; a one-line routine retry logs retry-declined
MESSAGE = ("[REPLY FRAME] This reply hands " + OWNER + " an action (\"{phrase}\") with no orange line. "
           "Write ONLY one line now, inside a diff fence: '! " + SYM_NEXT + " You <the action>'. Nothing else. "
           "If that phrase is not an action " + OWNER + " must take, write only: '" + SYM_ATTENTION + " <one-line result>.'")
MESSAGE_ASCII = ("[REPLY FRAME] This reply hands " + OWNER + " an action (\"{phrase}\") with no orange line. "
                 "Write ONLY one line now, inside a diff fence: '! <blue square U+1F7E6> You <the action>'. Nothing else. "
                 "If that phrase is not an action " + OWNER + " must take, write only: '<yellow square U+1F7E8> <one-line result>.'")
# P1: the first hit is a recommendation between options he owns -> ask for the decision line instead
MESSAGE_REC = ("[REPLY FRAME] This reply recommends between options " + OWNER + " owns (\"{phrase}\") with no decision line. "
               "Write ONLY one line now, inside a diff fence: '- " + SYM_DECISION + " Decision needed: <question>? "
               "Recommend: <option>; <tradeoff>'. Nothing else.")
MESSAGE_REC_ASCII = ("[REPLY FRAME] This reply recommends between options " + OWNER + " owns (\"{phrase}\") with no decision line. "
                     "Write ONLY one line now, inside a diff fence: '- <orange square U+1F7E7> Decision needed: <question>? "
                     "Recommend: <option>; <tradeoff>'. Nothing else.")
# Round 3: Claude Code in focus view (settings viewMode "focus") shows only the final message of a turn, so a
# one-line retry replaced the user's whole answer on screen (2026-10-31: the owner saw a lone 🟨 line and
# asked "???"). There the bounce asks for the whole reply again with the line added first.
_FOCUS = "Focus view shows only your final message, so write the reply again in full with one added line first, inside a diff fence: "
MESSAGE_FOCUS = ("[REPLY FRAME] This reply hands " + OWNER + " an action (\"{phrase}\") with no orange line. " + _FOCUS
                 + "'! " + SYM_NEXT + " You <the action>'. If that phrase is not an action " + OWNER + " must take, "
                 "write the reply again unchanged with no new line.")
MESSAGE_FOCUS_ASCII = ("[REPLY FRAME] This reply hands " + OWNER + " an action (\"{phrase}\") with no orange line. " + _FOCUS
                       + "'! <blue square U+1F7E6> You <the action>'. If that phrase is not an action " + OWNER + " must take, "
                       "write the reply again unchanged with no new line.")
MESSAGE_REC_FOCUS = ("[REPLY FRAME] This reply recommends between options " + OWNER + " owns (\"{phrase}\") with no decision line. " + _FOCUS
                     + "'- " + SYM_DECISION + " Decision needed: <question>? Recommend: <option>; <tradeoff>'. "
                     "If that is not a choice " + OWNER + " owns, write the reply again unchanged with no new line.")
MESSAGE_REC_FOCUS_ASCII = ("[REPLY FRAME] This reply recommends between options " + OWNER + " owns (\"{phrase}\") with no decision line. " + _FOCUS
                           + "'- <orange square U+1F7E7> Decision needed: <question>? Recommend: <option>; <tradeoff>'. "
                           "If that is not a choice " + OWNER + " owns, write the reply again unchanged with no new line.")
# the suite points this at a temp file; the live hook reads Claude Code's own settings
SETTINGS_PATH = os.environ.get("CLAUDE_SETTINGS_PATH") or os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def focus_view():
    """True when Claude Code's settings carry viewMode 'focus'. Read once per run; any error -> False (the old
    one-line bounce and grading)."""
    try:
        with open(SETTINGS_PATH, encoding="utf-8-sig") as fh:
            return json.load(fh).get("viewMode") == "focus"
    except Exception:
        return False


def content_lines(reply):
    """[(text, lang, fence_no)] for every non-empty line; fence delimiters dropped.
    lang '' outside fences, else the lower-cased fence tag ('plain' when untagged)."""
    out, lang, no = [], "", 0
    for raw in (reply or "").splitlines():
        if not raw.strip():
            continue
        m = FENCE_RE.match(raw)
        if m:
            if lang:
                lang = ""
            else:
                lang = (m.group(2) or "plain").lower()
                no += 1
            continue
        out.append((raw, lang, no if lang else 0))
    return out


def own_lines(reply):
    """The reply's own prose and color lines: quoted code or output fences dropped."""
    return [t for t in content_lines(reply) if t[1] in ("", "diff", "ini")]


def _norm(s):
    s = re.sub(r"[`*_>!\-\[\]#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _strip_code(s):
    return re.sub(r"`[^`]*`", " ", s)


def scan_text(reply):
    """The text G1 reads: the whole reply, fences included, with double-quoted spans and the status line's
    own 'next:' field blanked (same length, so offsets still point into the reply)."""
    blank = lambda q: " " * len(q.group(0))
    return STATUS_NEXT_RE.sub(blank, QUOTED_RE.sub(blank, reply or ""))


def prose_text(text):
    """scan_text() output with fenced blocks blanked too (same length): what the round-2 layers read."""
    out, inside = [], False
    for ln in (text or "").split("\n"):
        if FENCE_RE.match(ln):
            inside = not inside
            out.append(" " * len(ln))
        else:
            out.append(" " * len(ln) if inside else ln)
    return "\n".join(out)


def _negated_session(text, m):
    """A session/successor match whose own clause holds no/not/none/without/n't is not a hand-back."""
    if not SESSION_PART_RE.match(text, m.start()):
        return False
    lo = max(text.rfind(c, 0, m.start()) for c in ".;!?\n")
    his = [text.find(c, m.end()) for c in ".;!?\n"]
    hi = min([h for h in his if h >= 0] or [len(text)])
    return bool(NEGATOR_RE.search(text[lo + 1:hi]))


def _negated_before(text, m):
    """Round 3: a phrase or imper hit whose own clause carries a negator before the match ('Nothing is waiting
    on you', 'nothing waits on your go') is not a hand-back (live false bounce, 2026-10-31)."""
    lo = max(text.rfind(c, 0, m.start()) for c in ".;!?\n")
    seg = text[lo + 1:m.start()]
    # a conjunction starts a new sub-clause: 'nothing gets edited until we both agree ... and you say apply'
    # is still the user's go (replay, 2026-10-22)
    cuts = [c.end() for c in CONJ_RE.finditer(seg)]
    return bool(NEGATOR_BEFORE_RE.search(seg[cuts[-1]:] if cuts else seg))


def handback_hit(reply):
    """(layer, phrase) for the earliest hand-back in the reply, or None. layer is 'handback' (HANDBACK_RE,
    fences included, quoted spans blanked) or one of the prose layers imper / ask / phrase / rec."""
    text = scan_text(reply)
    cands = []
    for m in HANDBACK_RE.finditer(text):
        if not _negated_session(text, m):
            cands.append(("handback", m))
            break
    prose = prose_text(text)
    for key, rx in PROSE_LAYERS:
        for mm in rx.finditer(prose):
            if key in NEGATABLE_LAYERS and _negated_before(prose, mm):
                continue
            cands.append((key, mm))
            break
    if not cands:
        return None
    layer, m = min(cands, key=lambda c: c[1].start())
    # the phrase plus a little of what follows, for the bounce text and the log
    end = min(len(text), m.end() + 40)
    tail = text[m.end():end].split("\n")[0]
    phrase = (m.group(0) + tail).strip().replace('"', "'")
    return layer, re.sub(r"\s+", " ", phrase)[:70]


def handback(reply):
    """The first hand-back phrase in the reply, or None."""
    hit = handback_hit(reply)
    return hit[1] if hit else None


def has_orange(reply):
    """A line that hands the user the action in the v3 form: 🟦 or 🟧 at the start of a prose or diff line, or a
    diff '! ' line whose first word is You."""
    for raw, lang, _ in content_lines(reply or ""):
        if lang in ("", "diff") and ORANGE_START_RE.match(raw):
            return True
        if lang == "diff" and ORANGE_YOU_RE.match(raw):
            return True
    return False


def has_decision_line(reply):
    """A 🟧 at the start of a prose or diff line (the fix for a 'rec' bounce)."""
    return any(lang in ("", "diff") and DECISION_START_RE.match(raw) for raw, lang, _ in content_lines(reply or ""))


def is_routine_line(retry):
    """The retry is exactly one 🟨 line with no orange or decision marker (P5: the agent declined the bounce)."""
    rl = own_lines(retry)
    return (len(rl) == 1 and bool(ROUTINE_LINE_RE.match(rl[0][0]))
            and SYM_NEXT not in (retry or "") and SYM_DECISION not in (retry or ""))


def g1_hit(reply):
    """(layer, phrase) when the reply hands the user an action with no orange line, else None."""
    if has_orange(reply):
        return None
    return handback_hit(reply)


def g1(reply):
    """The matched hand-back phrase when the reply hands the user an action with no orange line, else None."""
    hit = g1_hit(reply)
    return hit[1] if hit else None


def is_orientation(reply):
    found = set()
    for raw, _, _ in own_lines(reply):
        m = LABEL_RE.match(raw)
        if m:
            s = m.group(1).strip().lower()
            found.add("For" if s.startswith("for") else "Big picture" if s.startswith("big")
                      else "Next" if s.startswith("next") else "Finding")
    return all(k in found for k in LABELS)


def own_chars(reply):
    """Characters of the reply's own text: quoted fences dropped, link targets stripped."""
    return sum(len(LINK_TARGET_RE.sub("]", raw).strip()) for raw, _, _ in own_lines(reply))


def evaluate(reply, machine=False, prev_final=None, first_reply=False, prompt="", detail=None):
    """Return (bounce_misses, fixes, notes) under the v3 rules.

    bounce_misses/fixes: G1 only (one orange or decision line corrects it).
    notes: G2, G3, G4 (already on screen; reported at the next prompt).
    prompt: G2 is skipped when it asks for detail (DETAIL_RE); detail, when given, overrides that match
    (Codex keeps only the flag). prev_final and first_reply are accepted for v2 callers and unused."""
    misses, fixes, notes = [], [], []
    hit = g1_hit(reply)
    if hit and hit[0] == "rec":
        misses.append("G1 recommendation with no decision line ('%s')" % hit[1])
        fixes.append("'- " + SYM_DECISION + " Decision needed: <question>? Recommend: <option>; <tradeoff>' inside a diff fence")
    elif hit:
        misses.append("G1 hand-back with no orange line ('%s')" % hit[1])
        fixes.append("'! " + SYM_NEXT + " You <the action>' inside a diff fence")
    if detail is None:
        detail = bool(DETAIL_RE.search(prompt or ""))
    text = reply or ""
    loud = SYM_DECISION in text or SYM_BLOCKED in text
    lines = own_lines(text)
    nchars = own_chars(text)
    if machine:
        cap_lines, cap_chars = MACHINE_CAP["loud" if loud else "quiet"]
        if len(lines) > cap_lines or nchars > cap_chars:
            notes.append("G4: a turn the user did not start ran %d lines (%d characters); one %s line unless it is a "
                         "decision or blocker" % (len(lines), nchars, SYM_ATTENTION))
    elif nchars > LONG_CAP and not loud and not detail and not is_orientation(text):
        notes.append("G2: %d characters with no decision or blocker; a routine turn is one '%s <result>.' line, "
                     "detail goes in the state file" % (nchars, SYM_ATTENTION))
    # a quoted span is not the agent's own boilerplate (a reply quoting his complaint, a rule, a log row)
    banned = sorted({re.sub(r"[*_\s]+", " ", m.group(0)).strip().lower()
                     for raw, _, _ in lines for m in BANNED_RE.finditer(QUOTED_RE.sub(" ", _strip_code(raw)))})
    if banned:
        notes.append("G3: it wrote %s; leave that out, absence is the message" % " and ".join("'%s'" % b for b in banned[:2]))
    return misses, fixes, notes


def signal_check(reply, machine=False, prev_final=None, first_reply=False, prompt=""):
    """Back-compatible wrapper: (bounce misses, fixes)."""
    m, f, _ = evaluate(reply, machine, prev_final, first_reply, prompt)
    return m, f


def merge_retry(original, retry):
    """What the user sees after a bounce: the bounced reply, then the retry."""
    if not original:
        return retry or ""
    return (original.rstrip() + "\n\n" + (retry or "").strip()).strip()


def restated(original, retry):
    """A retry that repeats the bounced reply instead of adding one line."""
    if not original or not retry:
        return False
    rl = own_lines(retry)
    if len(rl) > 3:
        return True
    old = {_norm(r) for r, _, _ in own_lines(original) if len(_norm(r)) >= 30}
    return any(_norm(r) in old for r, _, _ in rl)


def _prompt_text(obj):
    """The text of a real user prompt entry, or None."""
    if obj.get("type") != "user" or obj.get("isMeta"):
        return None
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if not parts:
            return None
        text = "\n".join(parts)
    else:
        return None
    if text.lstrip().startswith(NOT_A_PROMPT):
        return None
    return text


def stop_feedback_text(obj):
    """The text of a Stop hook's non-error feedback entry, or None. Shape (claude -p 2.1.316, 2026-10-31):
    {"type": "attachment", "attachment": {"type": "hook_additional_context", "hookEvent": "Stop",
    "content": ["[REPLY FRAME] ..."]}}. The hook_success attachment and the stop_hook_summary system entry of
    the same run carry the same text and are not counted."""
    if obj.get("type") != "attachment":
        return None
    att = obj.get("attachment")
    if not isinstance(att, dict) or att.get("type") != "hook_additional_context" or att.get("hookEvent") != "Stop":
        return None
    c = att.get("content")
    return "\n".join(str(x) for x in c) if isinstance(c, list) else str(c or "")


def parse_turn(transcript_path):
    """dict for the turn since the last real prompt or peer message:
    prompt, prompt_key, entrypoint, final (text after the last tool call or Stop-hook
    bounce), own_bounces, other_bounces, pre_bounce (the final before this hook's last
    bounce), pre_other (the final before another hook's last bounce), prev_final (the previous
    turn's reply as the user saw it), first_reply (no earlier turn had text)."""
    prompt, key, entry = None, "", ""
    segment, own, other, pre, pre_other = [], 0, 0, "", ""
    prev_final, turns_with_text = "", 0
    try:
        fh = open(transcript_path, encoding="utf-8", errors="replace")
    except OSError:
        return None

    def close_turn():
        nonlocal prev_final, turns_with_text
        if prompt is not None and segment:
            fin = "\n".join(segment).strip()
            prev_final = merge_retry(pre, fin) if pre else fin
            turns_with_text += 1

    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("isSidechain"):
                continue
            typ = obj.get("type")
            content = (obj.get("message") or {}).get("content")
            fb = stop_feedback_text(obj)
            if fb is not None:
                # 2026-10-31: the dispatcher returns a Claude Stop bounce as additionalContext; the transcript
                # carries it as an attachment entry (live probe), not a "Stop hook feedback" user entry
                if SELF_TAG in fb[:400] or "[REPLY FRAME]" in fb:
                    own += 1
                    pre = "\n".join(segment).strip() or pre
                else:
                    other += 1
                    pre_other = "\n".join(segment).strip() or pre_other
                segment = []
                continue
            if typ == "user":
                if isinstance(content, str) and content.startswith("Stop hook feedback"):
                    # whatever follows a bounce is a new final message
                    if SELF_TAG in content[:400] or "[REPLY FRAME]" in content:
                        own += 1
                        pre = "\n".join(segment).strip() or pre
                    else:
                        other += 1
                        pre_other = "\n".join(segment).strip() or pre_other
                    segment = []
                    continue
                if obj.get("isMeta") and isinstance(content, str) and content.lstrip().startswith(PEER_META):
                    # a peer message starts a machine turn of its own (G4)
                    close_turn()
                    prompt = "PEER_MESSAGE " + content[:200]
                    key = hashlib.sha1((str(obj.get("timestamp", "")) + content[:500]).encode("utf-8", "replace")).hexdigest()[:16]
                    entry = obj.get("entrypoint") or entry
                    segment, own, other, pre, pre_other = [], 0, 0, "", ""
                    continue
                text = _prompt_text(obj)
                if text is None:
                    continue
                close_turn()
                prompt = text
                key = hashlib.sha1((str(obj.get("timestamp", "")) + text[:500]).encode("utf-8", "replace")).hexdigest()[:16]
                entry = obj.get("entrypoint") or ""
                segment, own, other, pre, pre_other = [], 0, 0, "", ""
            elif typ == "assistant" and isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        segment = []
                    elif b.get("type") == "text" and (b.get("text") or "").strip():
                        segment.append(b["text"])
    if prompt is None:
        return None
    return {"prompt": prompt, "prompt_key": key, "entrypoint": entry,
            "final": "\n".join(segment).strip(), "own_bounces": own, "other_bounces": other,
            "pre_bounce": pre, "pre_other": pre_other, "prev_final": prev_final,
            "first_reply": turns_with_text == 0}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        for k in DEFAULT_CONFIG:
            if k in d:
                cfg[k] = bool(d[k])
    except Exception:
        pass
    return cfg


def log_row(sid, kind, why, mode, agent):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        new = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["timestamp_utc", "session_id", "kind", "why", "mode", "agent"])
            w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), sid, kind, why, mode, agent])
    except OSError:
        pass


def bounced_path(sid):
    return os.path.join(STATE_DIR, "reply_frame_bounced_%s.txt" % re.sub(r"[^\w-]", "_", sid))


def note_path(sid):
    return os.path.join(STATE_DIR, "reply_frame_note_%s.json" % re.sub(r"[^\w-]", "_", sid))


def write_notes(sid, notes):
    """Notes for the next prompt; response_style_contract.py injects and deletes them.
    Appends to notes not yet delivered (two noted replies before one prompt keep both)."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        old = []
        try:
            with open(note_path(sid), encoding="utf-8") as fh:
                old = json.load(fh).get("notes") or []
        except (OSError, ValueError, AttributeError):
            pass
        merged = []
        for n in list(old) + list(notes):
            if n not in merged:
                merged.append(n)
        with open(note_path(sid), "w", encoding="utf-8") as fh:
            json.dump({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": merged[-5:]}, fh)
    except OSError:
        pass


def pop_notes(sid):
    """Read and delete the notes for this session (called by the prompt hook)."""
    p = note_path(sid)
    try:
        with open(p, encoding="utf-8") as fh:
            notes = json.load(fh).get("notes") or []
        os.remove(p)
        return [str(n) for n in notes][:5]
    except (OSError, ValueError, AttributeError):
        return []


def codex_prompt_path(sid):
    key = hashlib.sha256(str(sid).encode("utf-8")).hexdigest()[:24]
    return os.path.join(STATE_DIR, "reply_frame_prompt_" + key + ".json")


def remember_codex_prompt(payload):
    """Called by a prompt hook (UserPromptSubmit) on Codex. Persist only the machine, bare and detail flags.

    Stop has a stable turn_id and last_assistant_message (Codex hooks docs),
    so the gate need not infer turn boundaries from evolving rollout formats.
    No raw prompt is retained. A Stop continuation must not change the flags.
    The detail flag (P6, round 2) is written only when true, so the file of an
    ordinary prompt keeps its earlier shape.
    """
    sid, prompt = payload.get("session_id"), payload.get("prompt")
    if not sid or not isinstance(prompt, str):
        return
    if prompt.lstrip().startswith(("[REPLY FRAME]", "Stop hook feedback")):
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        path = codex_prompt_path(sid)
        tmp = path + ".%d.tmp" % os.getpid()
        flags = {"machine": prompt.lstrip().startswith(MACHINE_PROMPT), "bare": bool(BARE_RE.search(prompt))}
        if DETAIL_RE.search(prompt):
            flags["detail"] = True
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(flags, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def codex_payload_turn(payload, sid):
    """Use documented Stop fields, independent of raw/converted transcript."""
    turn_id, final = payload.get("turn_id"), payload.get("last_assistant_message")
    if not isinstance(turn_id, str) or not turn_id or not isinstance(final, str):
        return None
    machine = bare = detail = False
    try:
        with open(codex_prompt_path(sid), encoding="utf-8") as fh:
            d = json.load(fh)
        machine, bare, detail = d.get("machine") is True, d.get("bare") is True, d.get("detail") is True
    except (OSError, ValueError, AttributeError):
        pass
    key = "codex:" + turn_id
    return {"prompt": "PEER_MESSAGE" if machine else ("just the answer" if bare else ""),
            "prompt_key": key, "entrypoint": "", "final": final, "own_bounces": 0,
            "other_bounces": 0, "pre_bounce": load_prebounce(sid, key), "pre_other": "", "prev_final": "",
            "first_reply": False, "detail": detail}


def prebounce_path(sid):
    key = hashlib.sha256(str(sid).encode("utf-8")).hexdigest()[:24]
    return os.path.join(STATE_DIR, "reply_frame_prebounce_" + key + ".json")


def save_prebounce(sid, prompt_key, final):
    """Codex has no transcript entry for the bounced reply; keep it so the retry is graded merged."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(prebounce_path(sid), "w", encoding="utf-8") as fh:
            json.dump({"key": prompt_key, "text": final}, fh, ensure_ascii=False)
    except OSError:
        pass


def load_prebounce(sid, prompt_key):
    try:
        with open(prebounce_path(sid), encoding="utf-8") as fh:
            d = json.load(fh)
        return (d.get("text") or "") if d.get("key") == prompt_key else ""
    except (OSError, ValueError, AttributeError):
        return ""


def read_stdin_utf8():
    """The payload as UTF-8 whatever the code page (hook_dispatch.read_stdin_utf8 has the history)."""
    stream = sys.stdin
    if stream is None:
        return ""
    buf = getattr(stream, "buffer", None)
    return buf.read().decode("utf-8", errors="replace") if buf is not None else stream.read()


def write_bounce(phrase, layer="handback", focus=False):
    """The bounce text on stderr; an ASCII spelling if the stream cannot carry the emoji.
    A 'rec' hit asks for the decision line, every other hit for the orange line; in focus view (Claude only)
    each asks for the whole reply again with that line added first."""
    if focus:
        msg, ascii_msg = ((MESSAGE_REC_FOCUS, MESSAGE_REC_FOCUS_ASCII) if layer == "rec"
                          else (MESSAGE_FOCUS, MESSAGE_FOCUS_ASCII))
    else:
        msg, ascii_msg = (MESSAGE_REC, MESSAGE_REC_ASCII) if layer == "rec" else (MESSAGE, MESSAGE_ASCII)
    try:
        sys.stderr.write(msg.format(phrase=phrase) + "\n")
    except UnicodeEncodeError:
        sys.stderr.write(ascii_msg.format(phrase=phrase.encode("ascii", "replace").decode("ascii")) + "\n")
    sys.stderr.flush()


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        payload = json.loads(read_stdin_utf8().lstrip("﻿") or "{}")
    except Exception:
        return 0
    cfg = load_config()
    if not cfg["enabled"]:
        return 0
    sid = str(payload.get("session_id") or "nosession")
    agent = "codex" if payload.get("_codex_rollout") or os.environ.get("CLAUDE_HOOK_AGENT") == "codex" else "claude"
    focus = agent == "claude" and focus_view()   # Codex has no focus view
    transcript = payload.get("transcript_path") or ""
    turn = codex_payload_turn(payload, sid) if agent == "codex" else None
    if turn is None:
        turn = parse_turn(transcript) if transcript and os.path.exists(transcript) else None
    if turn is None:
        return 0
    final = turn["final"]
    if agent == "codex" and isinstance(payload.get("last_assistant_message"), str):
        final = payload["last_assistant_message"].strip() or final
    mode, _, _ = effective_mode(sid, transcript)
    machine = turn["prompt"].lstrip().startswith(MACHINE_PROMPT)
    if agent == "codex":
        if REALTIME_SPOKEN_RE.match(final):
            log_row(sid, "exempt", "realtime spoken channel", mode, agent)
            return 0
        final = REALTIME_INLINE_RE.sub("", final, count=1)

    # Retry Stop after this hook's bounce: grade the retry by G1 on the bounced reply plus the retry.
    bp = bounced_path(sid)
    prior = ""
    try:
        prior = open(bp, encoding="utf-8").read().strip()
    except OSError:
        pass
    # The receipt stays until a new prompt (Codex review, 2026-10-02):
    # a host that repeats Stop without stop_hook_active can never re-arm it.
    prior_key, prior_stage = (prior.split("\n") + [""])[:2]
    if turn["own_bounces"] >= 1 or (prior_key and prior_key == turn["prompt_key"]):
        if prior_key == turn["prompt_key"] and prior_stage != "retried":
            pre_b = turn.get("pre_bounce") or ""
            if agent == "claude" and not pre_b:
                # the transcript parse missed the bounce entry (an unknown feedback shape): use the bounced reply
                # kept in state, and drop it from the front of the glued segment
                pre_b = load_prebounce(sid, turn["prompt_key"])
                if pre_b and final.startswith(pre_b):
                    final = final[len(pre_b):].strip()
            merged = merge_retry(pre_b, final)
            first = g1_hit(pre_b) if pre_b else None
            again = restated(pre_b, final)
            rnotes = ["the correction restated the reply; write only the one orange line"] if again else []
            if focus and pre_b:
                # focus view: the user sees only the retry, so grade it alone; restating is what was asked
                rnotes = []
                fp = first[1] if first else ""
                if _norm(final) == _norm(pre_b):
                    kind, why = "retry-declined", "focus: reply written again unchanged; G1 '%s'" % fp
                elif is_routine_line(final):
                    kind, why = "retry-declined", "focus: a one-line retry replaced the reply on screen; G1 '%s'" % fp
                else:
                    if first and first[0] == "rec":
                        still = None if has_decision_line(final) else fp
                    else:
                        still = g1(final)
                    kind, why = ("retry-still-wrong", "focus: G1 '%s'" % still) if still else ("retry-fixed", "focus")
            elif not again and is_routine_line(final):
                # P5: the agent read the hit as not the user's action and wrote one routine line (a measured false bounce)
                kind, why = "retry-declined", "declined G1 '%s'" % (first[1] if first else "")
                rnotes = []
            else:
                if first and first[0] == "rec":
                    # P1: a recommendation bounce is fixed only by a decision line at a line start
                    still = None if has_decision_line(merged) else first[1]
                else:
                    still = g1(merged)
                kind = "retry-still-wrong" if still else ("retry-restated" if again else "retry-fixed")
                why = "G1 '%s'" % still if still else "; ".join(rnotes)
            log_row(sid, kind, why[:300], mode, agent)
            if rnotes and (mode != "autopilot" or cfg["autopilot_bounce"] or cfg["autopilot_g1_bounce"]):
                write_notes(sid, rnotes)
            try:
                open(bp, "w", encoding="utf-8").write(prior_key + "\nretried")
            except OSError:
                pass
        return 0
    # stop_hook_active: stay quiet unless another hook's bounce caused this continuation
    if payload.get("stop_hook_active") and not turn.get("other_bounces"):
        return 0

    if not final:
        return 0
    if cfg["exempt_headless"] and turn["entrypoint"] == "sdk-cli":
        log_row(sid, "exempt", "headless", mode, agent)
        return 0
    if not machine and BARE_RE.search(turn["prompt"] or "") and len(final) <= 300:
        log_row(sid, "exempt", "bare answer asked", mode, agent)
        return 0

    _, _, notes = evaluate(final, machine, prompt=turn["prompt"] or "", detail=turn.get("detail") or None)
    # after another hook's bounce the user sees the reply above that bounce and this continuation together
    seen = merge_retry(turn.get("pre_other") or "", final) if turn.get("other_bounces") else final
    hit = g1_hit(seen)
    if not hit:
        if notes:
            log_row(sid, "note", "; ".join(notes)[:300], mode, agent)
            if mode != "autopilot" or cfg["autopilot_bounce"]:
                write_notes(sid, notes)
        else:
            log_row(sid, "pass", "v3" + ("; machine turn" if machine else ""), mode, agent)
        return 0
    layer, phrase = hit
    why = ("G1 recommendation: '%s'" if layer == "rec" else "G1 hand-back: '%s'") % phrase
    if mode == "autopilot" and not (cfg["autopilot_bounce"] or cfg["autopilot_g1_bounce"]):
        log_row(sid, "shadow", why, mode, agent)
        return 0
    log_row(sid, "bounce", why[:300], mode, agent)
    # Codex has no transcript entry for the bounced reply; Claude keeps it as a fallback for the parse
    save_prebounce(sid, turn["prompt_key"], final)
    if notes:
        write_notes(sid, notes)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        open(bp, "w", encoding="utf-8").write(turn["prompt_key"])
    except OSError:
        pass
    write_bounce(phrase, layer, focus)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
