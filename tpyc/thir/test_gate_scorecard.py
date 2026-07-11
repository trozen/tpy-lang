from .gate_scorecard import collect_gate_scorecard, format_gate_scorecard


def test_no_gate_scorecard_ratchet():
    score = collect_gate_scorecard()
    assert score.completed == (
        "root traversals",
        "statement preflights",
        "resumable leaf preflight",
    )
    assert score.remaining == (
        "comprehension preflight",
        "foreach preflight",
        "match prevalidation",
        "expression gate layer",
        "callable preflights",
    )
    assert format_gate_scorecard(score).splitlines()[0] == (
        "tpy| no-gate: 3/8 milestones complete")
