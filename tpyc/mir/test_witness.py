"""The definite-conflict witness (`witness.decide`) and the warnings built
from it: each rule positive and negative, the node cap, the reach oracle,
the hazard records each analysis emits, the collapse key, the Python
renderer and the recorded locations."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from .. import mir_diagnostics
from ..diagnostics import SourceLocation
from ..mir_diagnostics import MIRUnspellable, hazard_warnings
from ..thir.testutil import _compile, _entry
from . import witness as witness_module
from .collect import dump_codegen_mir, enumerate_body_sources, lower_and_analyze
from .dependencies import MIRReferent
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRPlace, MIRPoint, MIRRead, MIRReturn, MIRSlotId,
)
from .place_text import MIRStorageNames, spoken_place
from .reach import reaches, reaches_definitely
from .shapes import MIRContainerElements, MIRContainerStructure
from .test_payload_inspection import ALIAS, EXTRACT, READ, alias_function
from .test_payload_lifetime import CURRENT, ENTRY, SAVED, payload, write as payload_write
from .testutil import prepared
from .witness import MIREffect, MIRHazard, MIRHazardKind, MIRMayCause, MIRVerdict, decide

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
    assert not any(w.warns for w in verdicts)


def test_strict_reach_refuses_caller_seeded_loans(bodies) -> None:
    fn, analyses = bodies["through_param"]
    assert analyses.hazards, "the conflict is evidence"
    assert not any(w.warns for w in decide(analyses.prepared, analyses.hazards))


def test_an_element_write_under_an_element_borrow_is_a_may(bodies, warnings) -> None:
    # Indices carry no identity: `ps[j]` may be `ps[i]`.
    verdicts = _verdicts(bodies, "other_element")
    assert [w.verdict for w in verdicts if w.warns] == [MIRVerdict.MAY]
    assert (73, "this assignment may overwrite what 'p' refers to in place, and 'p' would see the new value on "
                "line 74; copy what 'p' refers to first, or use 'p' before this assignment") in warnings


@pytest.mark.parametrize(("name", "effect"), [
    ("replaced", MIREffect.FREES),      # a view's buffer replaced whole
    ("grows", MIREffect.MOVES),         # the list grows under its own walk
    ("other_element", MIREffect.OVERWRITES),  # an element rewritten where a reference to it lies
])
def test_a_warned_replacement_carries_its_effect(bodies, name, effect) -> None:
    warned = [w for w in _verdicts(bodies, name) if w.warns]
    assert warned and {w.effect for w in warned} == {effect}


def test_an_overwrite_is_shown_never_the_fallback(bodies) -> None:
    fn, analyses = bodies["grows"]
    hazard = next(h for h in analyses.hazards if h.kind is MIRHazardKind.REPLACEMENT)
    slots = {s.id: s for s in fn.slots}
    xs = next(iter(hazard.written)).place.root
    other = MIRSlotId(xs.body, xs.index + 100)

    def effect(written, retained):
        return witness_module.replacement_effect(
            replace(hazard, written=frozenset(written), retained=(frozenset(retained),)), slots, fn.record_layouts)

    structure = MIRReferent(MIRPlace(xs, (MIRContainerStructure(),)))
    # A shape write whose kept referent shows no in-place overwrite: another root, or a caller's loan.
    assert effect({structure}, {MIRReferent(MIRPlace(other, (MIRContainerElements(),)))}) is MIREffect.MOVES
    assert effect({structure}, {MIRReferent(MIRPlace(other), external=True, held=True)}) is MIREffect.MOVES
    # An element store under a holder of something inside that element (`rows[0] = [...]`): freed.
    element = MIRReferent(MIRPlace(xs, (MIRContainerElements(),)))
    inner = MIRReferent(MIRPlace(xs, (MIRContainerElements(), MIRContainerElements())))
    assert effect({element}, {inner}) is MIREffect.FREES
    # The same element held itself, by a holder that does not view it: overwritten in place.
    assert effect({element}, {element}) is MIREffect.OVERWRITES


def test_only_a_warned_replacement_has_an_effect(bodies) -> None:
    assert all(w.effect is None for name in ("block_view", "fill", "reassigned", "through_param")
               for w in _verdicts(bodies, name))


def test_a_whole_write_stays_definite(bodies) -> None:
    assert {w.verdict for w in _verdicts(bodies, "replaced") if w.warns} == {MIRVerdict.DEFINITE}


def test_the_reach_oracle_drops_every_step_left_to_the_caller() -> None:
    body = MIRBodyId("m", "f")
    xs, ys = MIRSlotId(body, 0), MIRSlotId(body, 1)
    structure = MIRReferent(MIRPlace(xs, (MIRContainerStructure(),)))
    elements = MIRReferent(MIRPlace(xs, (MIRContainerElements(),)))
    assert reaches_definitely(structure, elements)
    # Two element regions reach as one; the witness weighs the pair a MAY.
    assert reaches_definitely(elements, elements)
    held = MIRReferent(MIRPlace(ys), external=True, held=True)
    external = MIRReferent(MIRPlace(xs), external=True)
    assert reaches(external, held, lambda w, t: True) and not reaches_definitely(external, held)
    other = MIRReferent(MIRPlace(ys), external=True)
    assert reaches(external, other, lambda w, t: True) and not reaches_definitely(external, other)


def test_rule_one_is_a_may_when_some_pair_is_element_on_both_sides() -> None:
    body = MIRBodyId("m", "f")
    xs = MIRSlotId(body, 0)
    structure = MIRReferent(MIRPlace(xs, (MIRContainerStructure(),)))
    elements = MIRReferent(MIRPlace(xs, (MIRContainerElements(),)))
    inner_shape = MIRReferent(MIRPlace(xs, (MIRContainerElements(), MIRContainerStructure())))
    assert witness_module._all_pairs({structure}, {elements}) == (MIRVerdict.DEFINITE, None)
    assert witness_module._all_pairs({elements}, {elements}) == (MIRVerdict.MAY, MIRMayCause.ELEMENT)
    # An inner container's shape write passes through the outer elements region.
    assert witness_module._all_pairs({inner_shape}, {inner_shape}) == (MIRVerdict.MAY, MIRMayCause.ELEMENT)
    # Only some pair reaches: the holder only may keep the written storage.
    assert witness_module._all_pairs({elements}, {elements, structure}) == (MIRVerdict.MAY, MIRMayCause.PARTIAL)
    assert witness_module._all_pairs({structure}, {MIRReferent(MIRPlace(MIRSlotId(body, 1)))}) == (
        MIRVerdict.EVIDENCE, None)
    assert witness_module._all_pairs((), {elements}) == (MIRVerdict.EVIDENCE, None)


def test_a_loop_over_a_list_it_grows_warns_with_the_iteration(warnings) -> None:
    assert (86, "this call may move the elements of 'xs', but the iteration over 'xs' still walks them and "
                "continues on line 84; iterate over a copy ('copy(xs)'), or make the changes after the loop") in warnings


def test_a_block_end_warns_at_the_blocks_last_statement(warnings, bodies) -> None:
    assert (95, "'buf' is freed at the end of this block, but 'v' still views it and is read on line 96; "
                "create 'buf' before the block") in warnings
    # `t.s` holds buf on one path only (the join widened its referents): a MAY.
    verdicts = _verdicts(bodies, "block_view_chained")
    assert [(w.hazard.kind, w.verdict, w.cause) for w in verdicts] == [
        (MIRHazardKind.SCOPE_END, MIRVerdict.MAY, MIRMayCause.PARTIAL)]
    assert (103, "'buf' is freed at the end of this block, and 't.s' may still view it and is read on line 104; "
                 "create 'buf' before the block, or make 'Tok.s' an owned 'str'") in warnings


def test_a_temporary_is_spelled_by_its_expression(warnings) -> None:
    assert (108, "the temporary 'mk(c)' lives only until the end of this statement, but 't.s' still views it and "
                "is read on line 109; bind 'mk(c)' to a variable before this statement, or make 'Tok.s' an owned "
                "'str'") in warnings


def test_a_store_escape_warns_at_the_storing_statement_unless_rewritten(warnings, bodies) -> None:
    assert (118, "'buf' is freed when 'fill' returns, but 't.s' keeps a view of it; make 'Tok.s' an owned "
                 "'str'") in warnings
    verdicts = _verdicts(bodies, "fill_reset")
    assert [w.hazard.kind for w in verdicts] == [MIRHazardKind.STORE_ESCAPE] and not verdicts[0].warns


def test_a_return_escape_needs_exactly_one_body_origin(warnings, bodies) -> None:
    assert (129, "the return type 'StrView' lends the result, but 's' is created inside 'reassigned' and freed "
                 "when it returns; return an owned 'str' instead") in warnings
    verdicts = _verdicts(bodies, "either")
    assert [w.hazard.kind for w in verdicts] == [MIRHazardKind.RETURN_ESCAPE] and not verdicts[0].warns


def test_each_analysis_emits_its_hazards(bodies) -> None:
    kinds = {name: {h.kind for h in analyses.hazards} for name, (_fn, analyses) in bodies.items()}
    assert kinds["replaced"] == {MIRHazardKind.REPLACEMENT}
    assert kinds["block_view"] == {MIRHazardKind.SCOPE_END}
    assert kinds["fill"] == {MIRHazardKind.STORE_ESCAPE}
    assert kinds["reassigned"] == {MIRHazardKind.RETURN_ESCAPE}
    assert kinds["mk"] == set()


def test_no_other_body_warns(warnings) -> None:
    assert _lines(warnings) == [33, 33, 73, 86, 95, 103, 108, 118, 129]


def test_the_node_cap_leaves_a_hazard_evidence(bodies, monkeypatch) -> None:
    monkeypatch.setattr(witness_module, "NODE_CAP", 0)
    verdicts = _verdicts(bodies, "replaced")
    assert verdicts and all(w.capped and not w.warns for w in verdicts)


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


JOIN_SOURCE = '''\
from tpy import StrView


class H:
    def __init__(self, s: StrView) -> None:
        self.s = s


class R:
    def __init__(self, n: int) -> None:
        self.n = n


def mk(s: str) -> str:
    return s * 40


def cap_regression(f: bool, f0: bool, f1: bool, f2: bool, f3: bool, f4: bool, f5: bool, f6: bool, f7: bool,
                   f8: bool, f9: bool, f10: bool, f11: bool, f12: bool) -> None:
    buf = mk("a")
    h = H(buf)
    safe = H("safe")
    x = h if f else safe
    v0: StrView = ""
    v1: StrView = ""
    v2: StrView = ""
    v3: StrView = ""
    v4: StrView = ""
    v5: StrView = ""
    v6: StrView = ""
    v7: StrView = ""
    v8: StrView = ""
    v9: StrView = ""
    v10: StrView = ""
    v11: StrView = ""
    v12: StrView = ""
    buf = mk("z")
    if f0:
        v0 = x.s
    if f1:
        v1 = x.s
    if f2:
        v2 = x.s
    if f3:
        v3 = x.s
    if f4:
        v4 = x.s
    if f5:
        v5 = x.s
    if f6:
        v6 = x.s
    if f7:
        v7 = x.s
    if f8:
        v8 = x.s
    if f9:
        v9 = x.s
    if f10:
        v10 = x.s
    if f11:
        v11 = x.s
    if f12:
        v12 = x.s
    print(h.s[0])
    print(len(v0), len(v1), len(v2), len(v3), len(v4), len(v5), len(v6), len(v7), len(v8), len(v9), len(v10),
          len(v11), len(v12))


def read_locations(f: bool, g: bool) -> None:
    buf = mk("a")
    h = H(buf)
    safe = H("safe")
    x = h if f else safe
    buf = mk("z")
    print(x.s[0])
    if g:
        print("branch")
    print(h.s[0])


def pointer_loop_var(recs: list[R]) -> int:
    t = 0
    for r in recs:
        recs[0] = R(9)
        t += r.n
    return t


class Tok:
    def __init__(self, s: StrView, k: int) -> None:
        self.s = s
        self.k = k


def killed_on_every_branch(c: bool, f1: bool, flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    other = Tok("init", 2)
    x = t if c else other
    buf = mk("b")
    v: StrView = "init"
    w: StrView = "init"
    if f1:
        v = x.s
    if flag:
        w = x.s
        t.s = "safe"
    else:
        t.s = "safe"
    print(w[0])


def may_kill_then_definite_kill(c: bool, f1: bool, flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    other = Tok("init", 2)
    x = t if c else other
    buf = mk("b")
    v: StrView = "init"
    if f1:
        v = x.s
    if flag:
        x.s = "safe"
    else:
        t.s = "safe"
    print(t.s[0])


def correlated_copy_read(f: bool, c: bool) -> None:
    h = H("init")
    if f:
        buf = mk("b")
        h.s = buf
    v: StrView = "x"
    if c:
        v = h.s
    if c:
        print(v[0])
    else:
        print(h.s[0])


def may_join(f: bool, g: bool) -> None:
    buf = mk("a")
    h = H(buf)
    safe = H("safe")
    x = h if f else safe
    left: StrView = "safe"
    right: StrView = "safe"
    buf = mk("z")
    if g:
        left = x.s
    else:
        right = x.s
    h.s = "safe"
    print(left[0], right[0])


def both_reset(f: bool, g: bool) -> None:
    buf = mk("a")
    h = H(buf)
    safe = H("safe")
    x = h if f else safe
    left: StrView = "safe"
    right: StrView = "safe"
    buf = mk("z")
    if g:
        left = x.s
    else:
        right = x.s
    h.s = "safe"
    left = "safe"
    right = "safe"
    print(left[0], right[0])


def reset_selected(f: bool) -> None:
    buf = mk("a")
    a = H(buf)
    b = H(buf)
    x = a if f else b
    buf = mk("z")
    x.s = "safe"
    print(x.s[0])


def restore_member(f: bool) -> None:
    buf = mk("a")
    old = H(buf)
    a = H(buf)
    b = H(buf)
    x = a if f else b
    buf = mk("z")
    x.s = "safe"
    v: StrView = old.s
    a.s = v
    print(x.s[0])


def restore_member_safe_other(f: bool) -> None:
    buf = mk("a")
    old = H(buf)
    a = H(buf)
    b = H("safe")
    x = a if f else b
    buf = mk("z")
    x.s = "safe"
    v: StrView = old.s
    a.s = v
    print(x.s[0])


def alias_reset_by_other_name() -> None:
    buf = mk("a")
    old = H(buf)
    a = H("safe")
    b = a
    c = H("safe")
    d = c
    buf = mk("z")
    v: StrView = old.s
    a.s = v
    b.s = "safe"
    w: StrView = a.s
    c.s = w
    print(d.s[0])


def own_name_reset() -> None:
    buf = mk("a")
    old = H(buf)
    a = H("q")
    y = a
    buf = mk("z")
    v: StrView = old.s
    y.s = v
    a.s = "safe"
    print(y.s[0])


class Hr:
    def __init__(self, s: StrView) -> None:
        self.s = s

    def maybe_reset(self, flag: bool) -> None:
        if flag:
            self.s = "safe"


def callee_may_reset(flag: bool) -> None:
    buf = mk("a")
    old = Hr(buf)
    a = Hr("safe")
    y = a
    buf = mk("z")
    v: StrView = old.s
    y.s = v
    a.maybe_reset(flag)
    print(y.s[0])
'''


@pytest.fixture(scope="module")
def join_compiled():
    compiler, modules = _compile(JOIN_SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    return compiler, entry, ctx


@pytest.fixture(scope="module")
def join_bodies(join_compiled):
    return _bodies(join_compiled)


@pytest.fixture(scope="module")
def join_warnings(join_compiled):
    compiler, entry, ctx = join_compiled
    before = list(entry.analyzer.diagnostics)
    try:
        return {(d.loc.line, d.message) for _, d in compiler.report_mir_warnings([(entry, ctx)], strict=True)}
    finally:
        entry.analyzer.diagnostics[:] = before


def test_conditional_copies_of_a_joined_view_keep_the_definite_verdict_under_the_cap(join_bodies) -> None:
    # The strict walk chains no copy with two referents, so 13 conditional `vN = x.s` add no node key.
    warned = [w for w in _verdicts(join_bodies, "cap_regression") if w.warns]
    assert [(w.verdict, w.capped) for w in warned] == [(MIRVerdict.DEFINITE, False)]
    assert [p.index for p in warned[0].reads] and not any(w.capped for w in _verdicts(join_bodies, "cap_regression"))


def test_a_definite_verdict_cites_only_its_definite_reads(join_warnings) -> None:
    # `x.s[0]` (line 75) only may read buf; `h.s[0]` (line 78) does on every path.
    assert (74, "this assignment frees the old value of 'buf', but 'h.s' still views it and is read on line 78; "
                "give the new value its own name, or make 'H.s' an owned 'str'") in join_warnings


def test_an_element_write_under_a_loop_reaches_the_pointer_loop_variable(join_bodies) -> None:
    # The cursor depends on the shape only; the item it lends refers to an element.
    warned = [w for w in _verdicts(join_bodies, "pointer_loop_var") if w.warns]
    assert [(w.hazard.kind, w.verdict, w.cause) for w in warned] == [
        (MIRHazardKind.REPLACEMENT, MIRVerdict.MAY, MIRMayCause.ELEMENT)]


# A rebind through one name of an object ends the copy tracked under its other name (alias_reset_by_other_name,
# own_name_reset): the walk compares the storage a name re-roots to, not its spelling.
@pytest.mark.parametrize("name", ["killed_on_every_branch", "may_kill_then_definite_kill", "both_reset",
                                  "alias_reset_by_other_name", "own_name_reset"])
def test_a_holder_rebound_or_reset_on_every_path_stays_silent(join_bodies, name) -> None:
    assert not any(w.warns for w in _verdicts(join_bodies, name))


def test_a_write_through_a_joined_name_does_not_end_what_it_may_stand_for(join_bodies) -> None:
    # `x.s = "safe"` may rewrite either object, so the read of `x.s` after it may still see the old buffer:
    # a known false MAY (BUGS.md#mir-may-warning-correlated-conditions).
    warned = [w for w in _verdicts(join_bodies, "reset_selected") if w.warns]
    assert warned and all((w.verdict, w.cause) == (MIRVerdict.MAY, MIRMayCause.PARTIAL) for w in warned)


@pytest.mark.parametrize("name", ["restore_member", "restore_member_safe_other"])
def test_a_view_copied_into_a_field_is_read_through_the_objects_other_names(join_warnings, name) -> None:
    # `a.s = v` puts `old.s`'s freed buffer back into `a`, and `x.s` reads it when `x` is `a`: the copy is
    # chained at `a.s`, not only at the spelling `(*a).s`, so the warning names `old.s` and that read.
    lines = JOIN_SOURCE.splitlines()
    start = lines.index(f"def {name}(f: bool) -> None:")
    kill = lines.index('    buf = mk("z")', start) + 1
    read = lines.index("    print(x.s[0])", start) + 1
    assert (kill, f"this assignment frees the old value of 'buf', and 'old.s' may still view it and is read on "
                  f"line {read}; give the new value its own name, or make 'H.s' an owned 'str'") in join_warnings


def test_a_callee_store_that_only_may_run_does_not_end_the_copy(join_warnings) -> None:
    # `a.maybe_reset(flag)` stores into `a.s` only when flag holds: the copy `y.s` still views the freed buffer.
    lines = JOIN_SOURCE.splitlines()
    start = lines.index("def callee_may_reset(flag: bool) -> None:")
    kill = lines.index('    buf = mk("z")', start) + 1
    read = lines.index("    print(y.s[0])", start) + 1
    assert (kill, f"this assignment frees the old value of 'buf', and 'old.s' may still view it and is read on "
                  f"line {read}; give the new value its own name, or make 'Hr.s' an owned 'str'") in join_warnings


def test_a_copy_of_a_joined_view_read_after_its_source_is_reset_is_a_may(join_bodies) -> None:
    # `left = x.s` holds buf when f: the strict walk drops the copy (two referents), the MAY walk reads it.
    warned = [w for w in _verdicts(join_bodies, "may_join") if w.warns]
    assert [(w.verdict, w.cause) for w in warned] == [(MIRVerdict.MAY, MIRMayCause.PARTIAL)]


def test_the_use_walk_answers_at_the_node_cap_boundary() -> None:
    # A balanced CFG of exactly NODE_CAP + 1 blocks, every path reading the holder: the cap is checked
    # after a nonterminal node is expanded, so the walk still answers.
    body_id = MIRBodyId("cap_boundary", "f")
    holder, condition, result = (MIRSlotId(body_id, i) for i in range(3))
    count = witness_module.NODE_CAP // 2
    ids = [MIRBlockId(body_id, i) for i in range(2 * count + 1)]
    blocks, edges = {}, {}
    for i, bid in enumerate(ids):
        if i < count:
            children = [ids[2 * i + 1], ids[2 * i + 2]]
            blocks[bid] = MIRBlock(bid, (), MIRBranch(condition, *children))
            edges[bid] = children
        else:
            blocks[bid] = MIRBlock(bid, (MIRAssign(MIRPlace(result), MIRRead(MIRPlace(holder))),), MIRReturn(result))
            edges[bid] = []
    body = SimpleNamespace(blocks=blocks, state=lambda point: {}, successors=lambda block: edges[block.id],
                           rewrites=lambda point: [], rewrite_bases=lambda point: [])
    for mode in witness_module.MIRWalkMode:
        verdict, reads = witness_module._use_reached(body, MIRPoint(ids[0], 0), MIRPlace(holder), mode)
        assert verdict is True and len(reads) == count + 1


def test_a_copy_read_under_the_condition_that_made_it_stays_silent(join_bodies) -> None:
    # Known limitation: paths are not correlated, so `if c: v = h.s` ... `if c: print(v[0])` meets the
    # infeasible path that reads v without having copied it, and no walk reaches the use on every path.
    assert not any(w.warns for w in _verdicts(join_bodies, "correlated_copy_read"))


def test_a_payload_end_retained_beside_another_place_is_a_partial_may() -> None:
    # Hand-built: no source shape lowers a payload holder with a second referent (a narrow alias has one
    # source, and its stale read refuses validation), so the hazard is built as the analysis would emit it.
    loc = lambda line: SourceLocation(line=line, column=4)
    fn = alias_function(EXTRACT, replace(payload_write(False, 1), loc=loc(3)), replace(READ, loc=loc(4)))
    names = {CURRENT: "cur", ALIAS: "n", SAVED: "saved"}
    fn = replace(fn, slots=tuple(replace(s, name=names.get(s.id, s.name)) for s in fn.slots))
    ended = MIRReferent(payload(False))
    words = mir_diagnostics._Words(fn, MIRStorageNames(fn), lambda _: None, False)

    def verdict(retained: frozenset[MIRReferent]):
        (w,) = decide(prepared(fn), (MIRHazard(MIRHazardKind.PAYLOAD_END, MIRPoint(ENTRY, 2), payload(False),
                                                frozenset({ended}), MIRPlace(ALIAS), (retained,)),))
        return w, mir_diagnostics._payload_text(w, words)[0]

    # The payload retained by name: proven.
    w, text = verdict(frozenset({ended}))
    assert (w.verdict, w.cause) == (MIRVerdict.DEFINITE, None)
    assert text == "this assignment frees the value held in 'cur', but 'n' still refers to it and is used on line 4; " \
                   "copy it first"
    # Retained beside another payload: only some pair reaches.
    w, text = verdict(frozenset({ended, MIRReferent(payload(False, root=SAVED))}))
    assert (w.verdict, w.cause) == (MIRVerdict.MAY, MIRMayCause.PARTIAL)
    assert text == "this assignment frees the value held in 'cur', and 'n' may still refer to it and is used on " \
                   "line 4; copy it first"
