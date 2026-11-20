"""Red-team regression for reply_frame_gate G1 on the crafted set (tests/redteam_crafted.py).

Recorded by the red team (2026-10-30, on the unsanitized set): the phrase-list G1 caught 1 of 67 crafted
hand-backs; the structural rebuild caught 53 of 67, false-bounced 23 of 45 legitimate lines that use G1-like
words, and caught 4 of 4 marker-mention evasions. This test pins the rebuilt gate at no worse than those
numbers on the sanitized copy. The 23 of 45 false bounces are a known cost on adversarial crafted lines;
on 1,064 real replies the same change added 1 false bounce.
"""
import os

import pytest

import redteam_crafted as crafted
from conftest import HOOKS, load_module


@pytest.fixture(scope="module")
def gate(tmp_path_factory):
    os.environ["CLAUDE_HOOK_STATE_DIR"] = str(tmp_path_factory.mktemp("state"))
    try:
        return load_module("reply_frame_gate_rt", os.path.join(HOOKS, "reply_frame_gate.py"))
    finally:
        os.environ.pop("CLAUDE_HOOK_STATE_DIR", None)


def test_crafted_set(gate, capsys):
    caught = [t for t in crafted.H if gate.g1_hit(t)]
    false = [t for t in crafted.L if gate.g1_hit(t)]
    evasions = [t for t in crafted.O if gate.g1_hit(t)]
    with capsys.disabled():
        print("\n  crafted hand-backs caught: %d of %d" % (len(caught), len(crafted.H)))
        print("  legitimate lines bounced:  %d of %d" % (len(false), len(crafted.L)))
        print("  marker-mention evasions caught: %d of %d" % (len(evasions), len(crafted.O)))
    assert len(crafted.H) == 67 and len(crafted.L) == 45 and len(crafted.O) == 4
    assert len(caught) >= 53
    assert len(false) <= 23
    assert len(evasions) == 4
