"""No-alias obligations: a retention conflict between two parameters'
storage is published in the body's summary, each call discharges it, and a
call passing one object to both parameters is an `argument_alias` warning."""

from dataclasses import replace

import pytest

from .. import compiler as compiler_module
from ..diagnostics import Diagnostic, DiagnosticLevel, insert_in_source_order
from ..identity_map import IdentityMap
from ..parse import SourceLocation
from ..thir.testutil import _compile, _entry
from .testutil import no_layouts
from .call_contract import MIRAliasObligation, MIRParameterWrite, MIRSummaryState, summary_problem
from .shapes import MIRContainerElements, MIRContainerStructure
from .collect import enumerate_bodies
from .dependencies import MIRReferent
from .liveness import MIRPoint
from .nodes import MIRBlockId, MIRBodyId, MIRPlace, MIRSlotId
from .retention import MIRRetentionConflict, alias_obligation
from ..mir_diagnostics import obligation_callees, obligation_calls

SOURCE = '''\
from tpy import int32, copy


class H:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def setname(self, value: str, other: str) -> int32:
        self.name = other
        return len(value)


def bound(events: list[int32], tag: int32, value: str) -> int32:
    events.append(tag)
    return len(value)


def grow(xs: list[int32], tag: int32) -> None:
    xs.append(tag)


def same(xs: list[str], v: str) -> int32:
    xs.append("x")
    return len(v)


def keyword(xs: list[int32], names: list[str]) -> int32:
    return bound(xs, 1, value=names[0])


def assigned(h: H) -> int32:
    n = h.setname(h.name, "other")
    return n


def forward(xs: list[int32], names: list[str]) -> int32:
    return bound(xs, 1, names[0])


def twice(names: list[str]) -> int32:
    return same(names, names[0])


def set_first(xs: list[str], v: str) -> int32:
    xs[0] = "x"
    return len(v)


def other_element(xs: list[str]) -> int32:
    return set_first(xs, xs[1])


class Shelf:
    items: list[int32]

    def __init__(self, items: list[int32]) -> None:
        self.items = copy(items)

    def refill(self, data: list[int32]) -> None:
        self.items = copy(data)
        self.items = copy(data)


def refill_self(sh: Shelf) -> None:
    sh.refill(sh.items)


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def reset(self, h: Holder) -> int32:
        h.b = Box(0)
        return self.n


class Holder:
    b: Box

    def __init__(self) -> None:
        self.b = Box(1)

    def box(self) -> Box:
        return self.b


def reset_through_result(h: Holder) -> int32:
    return h.box().reset(h)


class Named:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


def touch(ps: list[Named], v: str) -> int32:
    for p in ps:
        p.s = "x"
    return len(v)


def element_field(ps: list[Named]) -> int32:
    return touch(ps, ps[0].s)


def maybe_rename(h: H | None, value: str) -> int32:
    if h is not None:
        h.name = "changed"
    return len(value)


def infeasible_rename(h: H) -> int32:
    flag: int32 | None = None
    if flag is not None:
        return maybe_rename(h, h.name)
    return 0


def main() -> None:
    print(keyword([1], ["a"]))


main()
'''


@pytest.fixture(scope="module")
def compiled():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    return compiler, entry, ctx


def _warnings(compiled) -> list[Diagnostic]:
    compiler, entry, ctx = compiled
    before = list(entry.analyzer.diagnostics)
    try:
        added = compiler.report_mir_warnings([(entry, ctx)], strict=True)
        assert [d for d in entry.analyzer.diagnostics if d not in before] == [d for _, d in added]
        return [d for _, d in added]
    finally:
        entry.analyzer.diagnostics[:] = before


def test_warnings_spell_the_arguments_as_written(compiled) -> None:
    found = {d.loc.line: d.message for d in _warnings(compiled)}
    # A body passing two of its own parameters publishes the obligation
    # again (`forward`, `keyword`); passing one list twice breaks it.
    assert sorted(found) == [34, 43, 52, 67, 92, 109, 121]
    # An assignment's statement starts at its target, before the call.
    assert found[34].startswith("argument 'h.name' is what 'H.setname' modifies "
                                "through parameter 'self' while it still uses 'value'")
    assert found[43].startswith("argument 'names[0]' points into 'names', which 'same'")
    # Element identity is not tracked: replacing `xs[0]` may reach `xs[1]`
    # (BUGS.md#borrowed-arg-into-mutated-storage).
    assert found[52].startswith("argument 'xs[1]' points into 'xs', which 'set_first' modifies "
                                "through parameter 'xs' while it still uses 'v'")
    # A whole container field replaced, as a record field is (`Shelf.refill`).
    assert found[67].startswith("argument 'sh.items' is what 'Shelf.refill' modifies "
                                "through parameter 'self' while it still uses 'data'")
    # A call-result receiver is spelled by the parameter it binds.
    assert found[92].startswith("the argument for parameter 'self' is what 'Box.reset' modifies "
                                "through parameter 'h' while it still uses 'self'")
    # A field write inside every element (`ps[elements].s`) reaches the
    # borrowed field of one.
    assert found[109].startswith("argument 'ps[0].s' is what 'touch' modifies "
                                 "through parameter 'ps' while it still uses 'v'")
    # A call in a branch presence proves infeasible still warns: its block's
    # own facts prove nothing absent, so the write reaches `h.name`.
    assert found[121].startswith("argument 'h.name' is what 'maybe_rename' modifies "
                                 "through parameter 'h' while it still uses 'value'")
    # A value-type argument is copied by binding it to a local.
    assert found[34].endswith("bind it to a local first ('v = h.name') or restructure so the call "
                              "does not hand over both")
    assert all(d.level is DiagnosticLevel.WARNING for d in _warnings(compiled))


def test_callers_of_two_parameters_publish_the_obligation(compiled) -> None:
    compiler, entry, ctx = compiled
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        verdicts = {v.name: v for v in enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name,
                                                        mir.definitions, compiler.thir_reject_by_node,
                                                        mir.workspace)}
    expected = MIRAliasObligation(2, (), MIRParameterWrite(0, (MIRContainerStructure(),)))
    # The body keeps each published obligation as a conflict of its own kind:
    # callers MIR does not analyze never check it.
    for name in ("bound", "forward"):
        verdict = verdicts[name]
        assert (verdict.conflicts == ("alias_obligation",)
                and verdict.summary.state is MIRSummaryState.KNOWN), verdict.describe()
    assert verdicts["bound"].summary.summary.obligations == frozenset({expected})
    # `forward` keeps `names[0]` apart from `xs`: its own parameter 1's elements.
    assert verdicts["forward"].summary.summary.obligations == frozenset({
        replace(expected, parameter=1, path=(MIRContainerElements(),))})
    # A body growing a list no other parameter reaches has nothing to publish.
    assert verdicts["grow"].summary.summary.obligations == frozenset()
    # A whole container field replaced while another parameter is still
    # read publishes `data` against `self.items`, as a record field does;
    # a caller passing `sh.items` to both breaks it.
    refill, = verdicts["refill"].summary.summary.obligations
    assert (refill.parameter, refill.path, refill.written.parameter) == (1, (), 0)
    assert [field.name for field in refill.written.path] == ["items"]
    assert verdicts["refill_self"].conflicts == ("argument_alias",)
    # A receiver a call returns by reference (`h.box()`, the storage of
    # `h.b`) beside `h`, whose `b` the method replaces while reading `self`.
    assert verdicts["reset_through_result"].conflicts == ("argument_alias",)
    assert verdicts["twice"].conflicts == ("argument_alias",)
    assert verdicts["keyword"].conflicts == ("alias_obligation",)
    # Element identity is not tracked: writing `xs[0]` may reach `xs[1]`,
    # an element step like any other, so the caller is warned.
    assert verdicts["other_element"].conflicts == ("argument_alias",)
    # A field written inside every element publishes `v` against `ps[elements].s`.
    touched, = verdicts["touch"].summary.summary.obligations
    assert (touched.parameter, touched.path, touched.written.parameter) == (1, (), 0)
    elements, field = touched.written.path
    assert isinstance(elements, MIRContainerElements) and field.name == "s"
    assert verdicts["element_field"].conflicts == ("argument_alias",)


def test_a_summary_cannot_publish_an_obligation_it_does_not_write(compiled) -> None:
    compiler, entry, ctx = compiled
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        known = next(r.summary for i, r in mir.workspace.summaries.items() if i.name == "bound")
    assert summary_problem(known, no_layouts()) is None
    unwritten = MIRAliasObligation(2, (), MIRParameterWrite(0, (MIRContainerElements(),)))
    assert summary_problem(replace(known, obligations=frozenset({unwritten})), no_layouts()) == (
        "invalid alias obligation write")
    itself = MIRAliasObligation(0, (), MIRParameterWrite(0, (MIRContainerStructure(),)))
    assert summary_problem(replace(known, obligations=frozenset({itself})), no_layouts()) == (
        "invalid alias obligation parameter")
    out_of_range = MIRAliasObligation(5, (), MIRParameterWrite(0, (MIRContainerStructure(),)))
    assert summary_problem(replace(known, obligations=frozenset({out_of_range})), no_layouts()) == (
        "invalid alias obligation parameter")


B = MIRBodyId("alias", "body")
P0, P1, G = (MIRSlotId(B, i) for i in range(3))
POINT = MIRPoint(MIRBlockId(B, 0), 0)


def _conflict(written: MIRReferent, retained: MIRReferent) -> MIRRetentionConflict:
    return MIRRetentionConflict(POINT, written, MIRPlace(P1), retained)


def test_only_two_parameters_external_storage_is_an_obligation() -> None:
    parameters = {P0: 0, P1: 1}

    def obligation(written: MIRReferent, retained: MIRReferent) -> MIRAliasObligation | str | None:
        return alias_obligation(_conflict(written, retained), parameters, {}, no_layouts())

    grown = MIRReferent(MIRPlace(P0, (MIRContainerStructure(),)), external=True)
    lent = MIRReferent(MIRPlace(P1), external=True)
    assert obligation(grown, lent) == MIRAliasObligation(
        1, (), MIRParameterWrite(0, (MIRContainerStructure(),)))
    # One parameter's own storage: the body conflicts whatever the caller passes.
    own = MIRReferent(MIRPlace(P0, (MIRContainerElements(),)), external=True)
    assert obligation(grown, own) is None
    # The caller's arguments can create these, but no obligation states them:
    # a stored loan's storage has no known place, a global is no parameter.
    held = MIRReferent(MIRPlace(P1), external=True, held=True)
    assert obligation(grown, held) == "a stored loan the caller chooses"
    assert obligation(held, lent) == "a stored loan the caller chooses"
    # A stored loan within one parameter is still the caller's choice.
    assert obligation(MIRReferent(MIRPlace(P1, (MIRContainerStructure(),)), external=True),
                      held) == "a stored loan the caller chooses"
    assert obligation(grown, MIRReferent(MIRPlace(G), external=True)) == "a global against the caller's arguments"
    assert obligation(MIRReferent(MIRPlace(G), external=True), lent) == "a global against the caller's arguments"
    # The body's own storage.
    assert obligation(MIRReferent(MIRPlace(P0, (MIRContainerStructure(),))),
                      MIRReferent(MIRPlace(P0, (MIRContainerElements(),)))) is None


def test_a_mir_failure_costs_only_its_body_and_never_fails_the_compile(compiled, monkeypatch) -> None:
    compiler, entry, ctx = compiled
    real = compiler_module.body_warnings

    def broken(fn, analyses, program, sites, **kwargs):
        if fn.id.declaration.startswith("assigned@"):
            raise RuntimeError("MIR defect")
        return real(fn, analyses, program, sites, **kwargs)

    monkeypatch.setattr(compiler_module, "body_warnings", broken)
    before = list(entry.analyzer.diagnostics)
    try:
        heard: list[str] = []
        added = compiler.report_mir_warnings([(entry, ctx)], on_failure=heard.append)
        # The other bodies' warnings survive the failure in `assigned`, and `-v` hears it.
        assert [d.loc.line for _, d in added] == [43, 52, 67, 92, 109, 121]
        assert len(heard) == 1 and "MIR defect" in heard[0]
        with pytest.raises(RuntimeError, match="MIR defect"):
            compiler.report_mir_warnings([(entry, ctx)], strict=True)
    finally:
        entry.analyzer.diagnostics[:] = before


@pytest.mark.parametrize("stage", ["mir_analysis", "lowered_bodies"])
def test_a_failure_before_any_body_returns_no_warnings(compiled, monkeypatch, stage) -> None:
    compiler, entry, ctx = compiled

    def broken(*_args, **_kwargs):
        raise RuntimeError("MIR defect")

    if stage == "mir_analysis":
        monkeypatch.setattr(compiler, "mir_analysis", broken)
    else:
        monkeypatch.setattr(compiler_module, "lowered_bodies", broken)
    before = list(entry.analyzer.diagnostics)
    heard: list[str] = []
    assert compiler.report_mir_warnings([(entry, ctx)], on_failure=heard.append) == []
    assert entry.analyzer.diagnostics == before
    assert len(heard) == 1 and "MIR defect" in heard[0]
    with pytest.raises(RuntimeError, match="MIR defect"):
        compiler.report_mir_warnings([(entry, ctx)], strict=True)


def test_the_verdict_pass_analyses_give_the_same_warnings(compiled) -> None:
    compiler, entry, ctx = compiled
    before = list(entry.analyzer.diagnostics)
    try:
        with compiler.mir_analysis([(entry, ctx)]) as mir:
            verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                        compiler.thir_reject_by_node, mir.workspace)
            analyzed = IdentityMap([(entry.ast, [(v.function, v.analyses) for v in verdicts if v.analyses is not None])])
            from_verdicts = compiler.report_mir_warnings([(entry, ctx)], mir, analyzed=analyzed, strict=True)
        entry.analyzer.diagnostics[:] = before
        alone = compiler.report_mir_warnings([(entry, ctx)], strict=True)
        assert [d.message for _, d in from_verdicts] == [d.message for _, d in alone] != []
    finally:
        entry.analyzer.diagnostics[:] = before


def test_a_later_phase_warning_lands_in_source_order() -> None:
    def warning(line: int, text: str) -> Diagnostic:
        return Diagnostic(DiagnosticLevel.WARNING, text, SourceLocation(line))

    diagnostics = [warning(3, "a"), warning(9, "c")]
    insert_in_source_order(diagnostics, warning(5, "b"))
    insert_in_source_order(diagnostics, warning(12, "d"))
    insert_in_source_order(diagnostics, Diagnostic(DiagnosticLevel.WARNING, "e"))
    assert [d.message for d in diagnostics] == ["a", "b", "c", "d", "e"]


CONSTRUCTION_SOURCE = '''\
from tpy import StrView, int32


class Buf:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text


class Tok:
    s: StrView

    def __init__(self, s: str, b: Buf) -> None:
        self.s = s
        b.text = "x" * 40


def made(b: Buf) -> int32:
    t = Tok(b.text, b)
    return len(t.s)


def apart(b: Buf, c: Buf) -> int32:
    t = Tok(c.text, b)
    return len(t.s)
'''


def test_a_construction_breaking_its_constructor_obligation_is_warned() -> None:
    compiler, modules = _compile(CONSTRUCTION_SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis([(entry, ctx)]) as mir:
        callees = obligation_callees(mir)
        bodies = {fn.name: fn for fn in ctx.thir_functions.values()}
        # The construction is the body's only call reaching an obligation: it is counted, so the
        # body is analyzed for argument aliases.
        assert obligation_calls(bodies["made"], callees, mir.definitions) == 1
        assert obligation_calls(bodies["apart"], callees, mir.definitions) == 1
    found = {d.loc.line: d.message for d in _warnings((compiler, entry, ctx))}
    # `s` may view `b.text`, which the constructor replaces while the record keeps `s`.
    assert sorted(found) == [20]
    assert found[20].startswith("argument 'b.text' is what 'Tok' modifies through parameter 'b' "
                                "while it still uses 's'")
