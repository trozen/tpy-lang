"""The definite-conflict witness (`witness.decide`) and the warnings built
from it: each rule positive and negative, the node cap, the reach oracle,
the hazard records each analysis emits, the collapse key, the Python
renderer and the recorded locations."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from ..mir_diagnostics import MIRUnspellable, hazard_warnings
from ..thir.testutil import _compile, _entry
from . import witness as witness_module
from .collect import dump_codegen_mir, enumerate_body_sources, lower_and_analyze
from .dependencies import MIRReferent
from .nodes import MIRBodyId, MIRPlace, MIRSlotId
from .place_text import MIRStorageNames, spoken_place
from .reach import reaches, reaches_definitely
from .shapes import MIRContainerElements, MIRContainerStructure
from .witness import MIRHazardKind, decide

SOURCE = '''\
from tpy import StrView, copy, int32


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: StrView, n: int32) -> None:
        self.s = s
        self.n = n


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def mk(c: str) -> str:
    return c * 40


def size(t: Tok) -> int32:
    return t.n


def replaced(flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    u = Tok(buf, 2)
    if flag:
        buf = mk("b")
    print(t.s, u.s)


def some_paths(c: bool) -> bool:
    buf = mk("a")
    v: StrView = buf
    if c:
        buf = mk("b")
    if not c:
        return v == "x"
    return False


def rebound_first() -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    buf = mk("b")
    t.s = "safe"
    print(t.s, buf)


def call_only(flag: bool) -> int32:
    buf = mk("a")
    t = Tok(buf, 1)
    if flag:
        buf = mk("b")
    return size(t)


def forever(flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    buf = mk("b")
    while flag:
        print(t.n)


def other_element(ps: list[P], i: int32, j: int32) -> int32:
    p = ps[i]
    ps[j] = P(9)
    return p.x


def through_param(t: Tok, xs: list[str]) -> int32:
    xs[0] = mk("z")
    return t.n + len(t.s)


def grows(xs: list[int32]) -> int32:
    t = 0
    for x in xs:
        if x < 0:
            xs.append(x)
        t += x
    return t


def block_view(c: bool) -> bool:
    v: StrView = "x"
    if c:
        buf = mk("a")
        v = buf
    return v == "x"


def block_view_chained(c: bool) -> bool:
    t = Tok("x", 1)
    if c:
        buf = mk("a")
        t.s = buf
    return t.s == "x"


def temp_view(c: str) -> bool:
    t = Tok(first(mk(c)), 1)
    return t.s == "x"


def first(s: StrView) -> StrView:
    return s


def fill(t: Tok, c: str) -> None:
    buf = mk(c)
    t.s = buf


def fill_reset(t: Tok, c: str) -> None:
    buf = mk(c)
    t.s = buf
    t.s = "lit"


def reassigned(s: str) -> StrView:
    s = s + "x"
    return s


def either(c: bool, p: P) -> P:
    q = copy(p)
    r = p
    if c:
        r = q
    return r
'''


@pytest.fixture(scope="module")
def compiled():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    return compiler, entry, ctx


def _bodies(compiled):
    """Each lowered body's function and analyses, by bare name, under the compilation."""
    compiler, entry, ctx = compiled
    found = {}
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        for source in enumerate_body_sources(entry.ast, entry.analyzer, ctx, entry.name,
                                             compiler.thir_reject_by_node):
            if source.refusal is None:
                fn, analyses = lower_and_analyze(source, mir.definitions, mir.workspace)
                if analyses is not None:
                    found[source.name] = (fn, analyses)
    return found


@pytest.fixture(scope="module")
def bodies(compiled):
    return _bodies(compiled)


@pytest.fixture(scope="module")
def warnings(compiled):
    compiler, entry, ctx = compiled
    before = list(entry.analyzer.diagnostics)
    try:
        return {(d.loc.line, d.message) for _, d in compiler.report_mir_warnings([(entry, ctx)], strict=True)}
    finally:
        entry.analyzer.diagnostics[:] = before


def _verdicts(bodies, name):
    fn, analyses = bodies[name]
    return [w for w in decide(analyses.prepared, analyses.hazards)]


def _lines(warnings):
    return sorted(line for line, _ in warnings)


def test_a_replacement_read_on_every_path_is_definite_once_per_holder(warnings) -> None:
    found = [text for line, text in warnings if line == 33]
    # Two holders of the replaced buffer: two warnings, each at the kill, each naming its own read.
    assert sorted(found) == [
        "this assignment frees the old value of 'buf', but 't.s' still views it and is read on line 34; "
        "give the new value its own name, or make 'Tok.s' an owned 'str'",
        "this assignment frees the old value of 'buf', but 'u.s' still views it and is read on line 34; "
        "give the new value its own name, or make 'Tok.s' an owned 'str'"]


@pytest.mark.parametrize("name", ["some_paths", "rebound_first", "call_only", "forever"])
def test_a_replacement_without_a_read_on_every_path_stays_evidence(bodies, name) -> None:
    verdicts = _verdicts(bodies, name)
    assert verdicts and all(w.hazard.kind is MIRHazardKind.REPLACEMENT for w in verdicts)
    assert not any(w.definite for w in verdicts)


@pytest.mark.parametrize("name", ["other_element", "through_param"])
def test_strict_reach_refuses_element_regions_and_caller_seeded_loans(bodies, name) -> None:
    fn, analyses = bodies[name]
    assert analyses.hazards, "the conflict is evidence"
    assert not any(w.definite for w in decide(analyses.prepared, analyses.hazards))


def test_the_reach_oracle_drops_every_step_left_to_the_caller() -> None:
    body = MIRBodyId("m", "f")
    xs, ys = MIRSlotId(body, 0), MIRSlotId(body, 1)
    structure = MIRReferent(MIRPlace(xs, (MIRContainerStructure(),)))
    elements = MIRReferent(MIRPlace(xs, (MIRContainerElements(),)))
    assert reaches_definitely(structure, elements)
    assert not reaches_definitely(elements, elements)
    held = MIRReferent(MIRPlace(ys), external=True, held=True)
    external = MIRReferent(MIRPlace(xs), external=True)
    assert reaches(external, held, lambda w, t: True) and not reaches_definitely(external, held)
    other = MIRReferent(MIRPlace(ys), external=True)
    assert reaches(external, other, lambda w, t: True) and not reaches_definitely(external, other)


def test_a_loop_over_a_list_it_grows_warns_with_the_iteration(warnings) -> None:
    assert (86, "this call may move the elements of 'xs', but the iteration over 'xs' still walks them and "
                "continues on line 84; iterate over a copy ('copy(xs)'), or make the changes after the loop") in warnings


def test_a_block_end_warns_at_the_blocks_last_statement(warnings, bodies) -> None:
    assert (95, "'buf' is freed at the end of this block, but 'v' still views it and is read on line 96; "
                "create 'buf' before the block") in warnings
    # Read through a borrow whose referents the join widened: evidence only.
    verdicts = _verdicts(bodies, "block_view_chained")
    assert [w.hazard.kind for w in verdicts] == [MIRHazardKind.SCOPE_END] and not verdicts[0].definite


def test_a_temporary_is_spelled_by_its_expression(warnings) -> None:
    assert (108, "the temporary 'mk(c)' lives only until the end of this statement, but 't.s' still views it and "
                "is read on line 109; bind 'mk(c)' to a variable before this statement, or make 'Tok.s' an owned "
                "'str'") in warnings


def test_a_store_escape_warns_at_the_storing_statement_unless_rewritten(warnings, bodies) -> None:
    assert (118, "'buf' is freed when 'fill' returns, but 't.s' keeps a view of it; make 'Tok.s' an owned "
                 "'str'") in warnings
    verdicts = _verdicts(bodies, "fill_reset")
    assert [w.hazard.kind for w in verdicts] == [MIRHazardKind.STORE_ESCAPE] and not verdicts[0].definite


def test_a_return_escape_needs_exactly_one_body_origin(warnings, bodies) -> None:
    assert (129, "the return type 'StrView' lends the result, but 's' is created inside 'reassigned' and freed "
                 "when it returns; return an owned 'str' instead") in warnings
    verdicts = _verdicts(bodies, "either")
    assert [w.hazard.kind for w in verdicts] == [MIRHazardKind.RETURN_ESCAPE] and not verdicts[0].definite


def test_each_analysis_emits_its_hazards(bodies) -> None:
    kinds = {name: {h.kind for h in analyses.hazards} for name, (_fn, analyses) in bodies.items()}
    assert kinds["replaced"] == {MIRHazardKind.REPLACEMENT}
    assert kinds["block_view"] == {MIRHazardKind.SCOPE_END}
    assert kinds["fill"] == {MIRHazardKind.STORE_ESCAPE}
    assert kinds["reassigned"] == {MIRHazardKind.RETURN_ESCAPE}
    assert kinds["mk"] == set()


def test_no_other_body_warns(warnings) -> None:
    assert _lines(warnings) == [33, 33, 86, 95, 108, 118, 129]


def test_the_node_cap_leaves_a_hazard_evidence(bodies, monkeypatch) -> None:
    monkeypatch.setattr(witness_module, "NODE_CAP", 0)
    verdicts = _verdicts(bodies, "replaced")
    assert verdicts and all(w.capped and not w.definite for w in verdicts)


def test_the_python_renderer_names_storage_by_its_source(bodies) -> None:
    fn, _analyses = bodies["grows"]
    names = MIRStorageNames(fn)
    xs = next(s for s in fn.slots if s.name == "xs")
    assert spoken_place(MIRPlace(xs.id, (MIRContainerElements(),)), names).region == "elements"
    cursor = next(s for s in fn.slots if s.shape.holding.name == "CURSOR")
    spoken = spoken_place(MIRPlace(cursor.id), names)
    assert (spoken.name, spoken.origin) == ("xs", "iteration")
    fn, _analyses = bodies["replaced"]
    names = MIRStorageNames(fn)
    # The record `t` borrows whole is spelled by `t`, no `@line` or `%N`.
    bound = next(place for place in names.bound if names.spoken[place] == "t")
    assert spoken_place(bound, names).origin == "bound"


def test_a_temporary_records_its_expression_span(bodies) -> None:
    fn, _analyses = bodies["temp_view"]
    spans = {(s.span.line, s.span.column, s.span.end_column) for s in fn.slots
             if s.kind.name == "TEMPORARY" and s.span is not None and s.shape.owns_storage}
    # The span is the call expression alone, not its statement.
    assert (108, 18, 23) in spans


def test_an_unspellable_place_falls_back_outside_a_strict_run(bodies) -> None:
    fn, analyses = bodies["temp_view"]
    spanless = replace(fn, slots=tuple(replace(slot, span=None) for slot in fn.slots))
    sites = SimpleNamespace(source_text=lambda loc: None)
    (warning,) = hazard_warnings(spanless, analyses, sites)
    assert warning.message.startswith("a temporary value is freed "), warning.message
    assert "'a temporary value'" not in warning.message
    assert warning.message.endswith("bind the value to a variable that outlives the block, or make 'Tok.s' an owned 'str'")
    with pytest.raises(MIRUnspellable):
        hazard_warnings(spanless, analyses, sites, strict=True)


def test_a_capped_witness_shows_in_the_dump(compiled, monkeypatch) -> None:
    compiler, entry, ctx = compiled
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        assert "witness cap reached" not in dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name,
                                                             mir.definitions, compiler.thir_reject_by_node,
                                                             mir.workspace)
        monkeypatch.setattr(witness_module, "NODE_CAP", 0)
        dump = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                compiler.thir_reject_by_node, mir.workspace)
    # `replaced` has two hazards whose use witness gives up at once.
    assert "witness cap reached: 2 hazards" in dump
