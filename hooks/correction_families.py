"""Correction-family regexes used by session_mode.py's correction latch.

Each family is a class of short user corrections that the private harness counted
in its saved transcripts (a baseline measurement, 2026-09-06). session_mode.py
records a short typed prompt that matches one of these, verbatim, so later turns
of the same session can be reminded of it. The lists were tuned on one user's
transcripts; treat them as an example, not a general taxonomy.
"""

FAMILIES = {
    "F2_push_forward": r"\b(dont stop|don't stop|keep going|keep pushing|push forward|auto ?pilot|are you working|still not done|what are you waiting)\b",
    "F4_discuss_first": r"\b(discuss first|lets discuss|let's discuss|discuss here|dont rush|don't rush|slow down|jump(ing)? too fast|plan first|just print the instruction)\b",
    "F3_too_long": r"\b(too long|too wordy|wordy|more concise|shorter|cannot read all|band ?width is limited)\b",
    "F3_unclear": r"\b(what do you mean|i dont understand|i don't understand|not clear|dont understand|in one sentence)\b",
    "F1_check": r"\b(are you sure|check again|check that|did you (ask|check|open|send)|really done|not done|still not|dont see|don't see|open (folder|it) again)\b",
    "F5_lane": r"\b(no need|not needed|dont worry|don't worry|stay in your line|stay focused|not your job|drop this reminder)\b",
    "F6_model_choice": r"\b(fable|opus|sonnet)\b",
    "F3_voice": r"\b(not my language|my writing style|my voice|sounds like ai|humanizer)\b",
}
