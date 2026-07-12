from .gate_scorecard import collect_gate_scorecard, format_gate_scorecard


def test_no_gate_scorecard_ratchet():
    score = collect_gate_scorecard()
    assert score.preflight_symbols == ()
    assert score.expression_gate_present
    assert score.expression_gate_imports > 0
    assert score.expression_gate_consumers

    lines = format_gate_scorecard(score).splitlines()
    assert lines[0] == "tpy| no-gate: 0 known predictive preflight symbols"
    assert lines[1].startswith("tpy| expression gates: present;")
