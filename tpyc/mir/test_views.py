"""StrView / BytesView as MIR places: a view is a readonly borrowed holder
typed by its view, whose referents are the owned-leaf storage its source
reads. Every view-producing operation transfers referents, an owning sink
copies through the view, and the view conflicts are the existing structural
kinds (replacement, scope end, return escape)."""

import re
from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing, latch_declared_native_flags
from ..typesys import BIGINT, BYTESVIEW, INT32, STRVIEW
from .call_contract import MIRSummaryState, stub_summary
from .collect import MIRVerdictStatus, analyze_body, enumerate_bodies
from .dependencies import MIRReferent
from .dump import dump_function
from .liveness import MIRPoint
from .lower import lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRBorrow, MIRConstant, MIRCopy, MIRDeref, MIRField, MIRFieldId, MIRFunction,
    MIRPlace, MIRReturn, MIRSlot, MIRSlotKind, MIRValueKind,
)
from .storage_evidence import MIRStorageConflictKind, analyze_return_escapes
from .validate import MIRValidationError, validate_function

SOURCE = """\
from tpy import StrView, String, BytesView, int32, pure
from tpy.extern import native

@pure
@native("probe_nullary_view")
def nullary_view() -> StrView: ...

@pure
@native("probe_view")
def probe_view(s: str) -> StrView: ...

def takes(s: str) -> int: return len(s)
def view_param(s: str) -> int:
    v = s
    w = v
    return len(w)
def slice_local(s: str) -> int:
    v = s[1:3]
    return len(v)
def lo() -> int32:
    return 1
def slice_bounds(s: str, b: int32) -> int:
    v = s[lo():b]
    return len(v)
def slice_of_owned(a: str, b: str) -> int:
    t = a + b
    v = t[1:]
    return len(v)
def literal_view() -> int:
    v = "hello"
    return len(v)
def view_return(s: str) -> StrView:
    return s[1:]
def view_return_caller(s: str) -> int:
    v = view_return(s)
    return len(v)
def owned_from_view(s: str) -> str:
    v = s[1:]
    return v
def explicit_view_param(v: StrView) -> StrView:
    return v
def view_param_slice(v: StrView) -> int:
    w = v[1:]
    return len(w)
def string_as_str_param(x: String) -> int:
    return takes(x)
def string_as_str_sink(x: String) -> str:
    t: str = x
    return t
def bytes_slice(b: bytes) -> int:
    v = b[1:]
    return len(v)
def stepped(s: str) -> int:
    return len(s[::2])
def reassigned_param(s: str) -> int:
    s = s + "x"
    v: StrView = s[1:]
    return len(v)
def reassigned_view_param(v: StrView, s: str) -> int:
    v = s
    return len(v)
def loop_rebind(a: str, b: str, n: int32) -> int:
    v = a
    i = 0
    while i < n:
        v = b
        i += 1
    return len(v)
def live_conflict(a: str) -> int:
    t = a + "x"
    c: StrView = t
    t += "y"
    return len(c)
def bytes_live_conflict(a: bytes) -> int:
    t = a + b"x"
    c: BytesView = t
    t += b"y"
    return len(c)
def last_use(a: str) -> int:
    t = a + "x"
    c: StrView = t
    n = len(c)
    t += "y"
    return n + len(t)
G = "static"
def returns_global_view() -> StrView:
    return G
def returns_choice(s: str, c: bool) -> StrView:
    if c:
        return s
    return "lit"
def reads(s: str, t: str) -> bool:
    v = s[1:]
    w = t[1:]
    print(v)
    return v == "x" and v < w and v[0] == w[0]
def view_to_takes(s: str) -> int:
    v = s[1:]
    return takes(v)
def stub_lent(s: str) -> int:
    v = probe_view(s)
    return len(v)
def stub_nullary() -> int:
    v = nullary_view()
    return len(v)
def escape_source(a: str) -> StrView:
    t = a + "x"
    return a[1:]
"""


@dataclass(frozen=True)
class _Views:
    compiler: object
    modules: list
    definitions: object
    thir: dict
    verdicts: dict


@pytest.fixture(scope="module")
def views():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = {v.name: v for v in enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name,
                                                        mir.definitions, compiler.thir_reject_by_node,
                                                        mir.workspace)}
        yield _Views(compiler, modules, mir.definitions, functions, verdicts)


@pytest.fixture
def active(views):
    # The per-test state reset clears the stub facts latched onto the static
    # TypeDefs (`borrowing_view` included, which every view rule reads); the
    # compilation latched them from the parsed stubs, so latch them again.
    for module in views.modules:
        for record in module.ast.all_records():
            if record.builtin_type_key:
                latch_declared_native_flags(record.builtin_type_key, record)
    with activate_compiler(views.compiler):
        yield views


def _fn(views: _Views, name: str) -> MIRFunction:
    fn = views.verdicts[name].function
    assert isinstance(fn, MIRFunction), views.verdicts[name].describe()
    return fn


def _lines(fn: MIRFunction) -> list[str]:
    return [re.sub(r" @ \d+:\d+$", "", line.strip()) for line in dump_function(fn).splitlines()]


def _slot(fn: MIRFunction, name: str) -> MIRSlot:
    return next(s for s in fn.slots if s.name == name)


def _at_return(views: _Views, name: str) -> dict:
    """Each holder's referents at the body's (single) return, by slot name."""
    fn, analyses = _fn(views, name), views.verdicts[name].analyses
    block = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    state = analyses.dependencies.referents[MIRPoint(block.id, len(block.statements))]
    names = {s.id: s.name for s in fn.slots}
    return {names[leaf.root]: refs for leaf, refs in state.items() if names[leaf.root] is not None}


def _external(fn: MIRFunction, *names: str) -> frozenset[MIRReferent]:
    return frozenset(MIRReferent(MIRPlace(_slot(fn, n).id), external=True) for n in names)


# --- the acceptance program --------------------------------------------------------

def test_view_holders_are_readonly_borrows_typed_by_the_view(active) -> None:
    fn = _fn(active, "view_param")
    for name in ("v", "w"):
        slot = _slot(fn, name)
        assert (slot.type, slot.value_kind, slot.readonly, slot.kind) == (
            STRVIEW, MIRValueKind.BORROWED, True, MIRSlotKind.LOCAL)
    lines = _lines(fn)
    assert "%1 = borrow (*%0)" in lines and "%2 = alias %1" in lines
    assert _at_return(active, "view_param")["w"] == _external(fn, "s")


def test_unstepped_slices_borrow_the_whole_receiver_after_their_bounds(active) -> None:
    assert "%1 = borrow (*%0)" in _lines(_fn(active, "slice_local"))
    # A call bound runs before the borrow; it is an ordinary read.
    lines = _lines(_fn(active, "slice_bounds"))
    call = next(i for i, line in enumerate(lines) if "call main::lo()" in line)
    assert lines.index("%2 = borrow (*%0)") > call
    # An owned local is borrowed in place, never copied.
    fn = _fn(active, "slice_of_owned")
    t, v = _slot(fn, "t"), _slot(fn, "v")
    assert f"%{v.id.index} = borrow %{t.id.index}" in _lines(fn)
    assert _at_return(active, "slice_of_owned")["v"] == frozenset({MIRReferent(MIRPlace(t.id))})
    # A slice of a view parameter aliases it.
    assert "%1 = alias %0" in _lines(_fn(active, "view_param_slice"))
    # The bytes family follows the same rule.
    fn = _fn(active, "bytes_slice")
    assert _slot(fn, "v").type == BYTESVIEW and "%1 = borrow (*%0)" in _lines(fn)


def test_a_static_literal_is_its_holders_immortal_origin(active) -> None:
    fn = _fn(active, "literal_view")
    assert "%0 = 'hello'" in _lines(fn)
    assert _at_return(active, "literal_view")["v"] == _external(fn, "v")


def test_view_results_carry_their_parameter_origins_to_the_caller(active) -> None:
    fn = _fn(active, "view_return")
    assert fn.borrowed_result == th.THIRBorrowedRecord(STRVIEW, True)
    assert "result borrowed readonly" in _lines(fn) and "%1 = borrow (*%0)" in _lines(fn)
    summary = active.verdicts["view_return"].summary
    assert summary.state is MIRSummaryState.KNOWN and summary.summary.returns == frozenset({0})
    caller = _fn(active, "view_return_caller")
    assert any(line.startswith("%1 = call main::view_return(%2)") and "returns={param0}" in line
               for line in _lines(caller))
    assert _at_return(active, "view_return_caller")["v"] == _external(caller, "s")
    # A view parameter is the caller's loan passed by value; returning it returns that loan.
    explicit = _fn(active, "explicit_view_param")
    slot = _slot(explicit, "v")
    assert (slot.kind, slot.value_kind, slot.readonly, slot.passing.name) == (
        MIRSlotKind.PARAMETER, MIRValueKind.BORROWED, True, "VALUE")
    assert active.verdicts["explicit_view_param"].summary.summary.returns == frozenset({0})


def test_owning_sinks_copy_through_the_view(active) -> None:
    lines = _lines(_fn(active, "owned_from_view"))
    assert "%3 = copy (*%1) may-raise [initialize_once]" in lines and lines[-1] == "return %3"


def test_a_string_bound_as_str_is_lent_or_copied(active) -> None:
    # At a str view parameter the String is lent as a view of its storage.
    lines = _lines(_fn(active, "string_as_str_param"))
    assert "%2: StrView readonly-ref temporary residence=r0" in lines
    assert "%2 = borrow (*%0)" in lines and any(line.startswith("%1 = call main::takes(%2)") for line in lines)
    # At an owning sink it is copied.
    assert "%1 = copy (*%0) may-raise [initialize_once]" in _lines(_fn(active, "string_as_str_sink"))


def test_a_view_is_lent_where_a_str_view_parameter_takes_it(active) -> None:
    lines = _lines(_fn(active, "view_to_takes"))
    assert any(line.startswith("%3 = call main::takes(%1)") for line in lines)


def test_a_reassigned_str_parameter_is_viewed_through_its_copy(active) -> None:
    fn = _fn(active, "reassigned_param")
    param, local = (s for s in fn.slots if s.name == "s")
    assert param.kind is MIRSlotKind.PARAMETER and local.value_kind is MIRValueKind.OWNED
    assert _at_return(active, "reassigned_param")["v"] == frozenset({MIRReferent(MIRPlace(local.id))})


def test_a_loop_rebinding_joins_both_sources(active) -> None:
    fn = _fn(active, "loop_rebind")
    assert _at_return(active, "loop_rebind")["v"] == _external(fn, "a", "b")


def test_a_view_reads_its_leaf_through_the_borrow(active) -> None:
    fn = _fn(active, "reads")
    v, w = _slot(fn, "v").id.index, _slot(fn, "w").id.index
    lines = _lines(fn)
    assert f"print (%{v})" in lines and f"%{v} = borrow (*%0)" in lines
    assert any(re.fullmatch(rf"%\d+ = %{v} == %\d+", line) for line in lines)
    assert any(re.fullmatch(rf"%\d+ = %{v} < %{w}", line) for line in lines)
    assert any(re.fullmatch(rf"%\d+ = op getitem \(%{v}, %\d+\) may-raise", line) for line in lines)
    # `len` dispatches to the viewed leaf's runtime code: the stub reads the view in place.
    assert "%1 = call stub tpy._builtins._funcs.len[Sized](%1)" not in _lines(_fn(active, "slice_local"))
    assert any(line.startswith("%5 = call stub tpy._builtins._funcs.len[Sized](%1)")
               for line in _lines(_fn(active, "slice_local")))


def test_view_verdicts(active) -> None:
    covered = ("view_param", "slice_local", "slice_bounds", "slice_of_owned", "literal_view", "view_return",
               "view_return_caller", "owned_from_view", "explicit_view_param", "view_param_slice",
               "string_as_str_param", "string_as_str_sink", "bytes_slice", "reassigned_param", "loop_rebind",
               "last_use", "returns_global_view", "returns_choice", "reads", "view_to_takes", "stub_lent")
    for name in covered:
        verdict = active.verdicts[name]
        assert verdict.status is MIRVerdictStatus.COVERED and verdict.conflicts == (), (name, verdict.describe())
    for name, reason in (("stepped", "stepped slice"), ("reassigned_view_param", "reassigned view parameter"),
                         ("stub_nullary", "stub view result has no lent origin")):
        verdict = active.verdicts[name]
        assert verdict.status is MIRVerdictStatus.UNCOVERED and verdict.reason == reason, (name, verdict.describe())


# --- conflicts ------------------------------------------------------------------------

def test_mutating_a_viewed_source_while_the_view_is_live_is_a_replacement(active) -> None:
    for name in ("live_conflict", "bytes_live_conflict"):
        verdict = active.verdicts[name]
        assert verdict.status is MIRVerdictStatus.COVERED and verdict.conflicts == ("replacement",), name
        fn, conflict = verdict.function, verdict.analyses.retention.conflicts[0]
        assert conflict.affected == MIRReferent(MIRPlace(_slot(fn, "t").id))
        assert conflict.holder == MIRPlace(_slot(fn, "c").id)
    # The same write after the view's last use conflicts with nothing.
    assert active.verdicts["last_use"].conflicts == ()


def test_return_escape_is_discovered_for_every_lowered_body(active) -> None:
    # THIR damage: the returned slice views the body's own local, which sema
    # refuses at the source; MIR still lowers it and names the escape.
    source = active.thir["escape_source"]
    damaged = replace(source, body=tuple(
        replace(stmt, value=replace(stmt.value, receiver=th.THIRName(
            result_type=source.body[0].resolved_type, name="t", form=th.Form.BORROW)))
        if isinstance(stmt, th.THIRReturn) else stmt for stmt in source.body))
    fn = lower_function(damaged, MIRBodyId("views", "escape"), definitions=active.definitions)
    assert isinstance(fn, MIRFunction)
    analyses = analyze_body(fn)
    assert "return_escape" in analyses.conflicts
    escape, = analyses.escapes
    assert escape.kind is MIRStorageConflictKind.RETURN_ESCAPE and escape.origin == MIRPlace(_slot(fn, "t").id)
    # The undamaged body returns a view of its parameter: no escape.
    assert analyze_return_escapes(_fn(active, "escape_source"),
                                  active.verdicts["escape_source"].analyses.dependencies) == ()


# --- summaries --------------------------------------------------------------------------

def test_a_view_result_outside_the_parameters_refuses_its_summary(active) -> None:
    # A global handle and a static literal have no parameter index; the bodies still lower.
    for name in ("returns_global_view", "returns_choice"):
        summary = active.verdicts[name].summary
        assert summary.state is MIRSummaryState.OPAQUE
        assert summary.reason == "view result origin outside the parameters"
        assert active.verdicts[name].status is MIRVerdictStatus.COVERED


def test_a_view_stub_borrows_what_it_lends_or_refuses(active) -> None:
    lent = next(c for c in _thir_calls(active.thir["stub_lent"]) if c.stub_callee.identity.qualified_name.endswith(
        "probe_view")).stub_callee
    summary = stub_summary(lent)
    assert summary.returns == frozenset({0}) and summary.borrowed_result == th.THIRBorrowedRecord(STRVIEW, True)
    fn = _fn(active, "stub_lent")
    assert _at_return(active, "stub_lent")["v"] == _external(fn, "s")
    nullary = _thir_calls(active.thir["stub_nullary"])[0].stub_callee
    # An empty origin set is never read as fresh for a view.
    assert stub_summary(nullary) == "stub view result has no lent origin"


def _thir_calls(fn: th.THIRFunction) -> list[th.THIRCall]:
    found: list[th.THIRCall] = []

    def walk(node: object) -> None:
        if isinstance(node, th.THIRCall):
            found.append(node)
        if isinstance(node, tuple):
            for item in node:
                walk(item)
        elif isinstance(node, th.THIRNode):
            for name in node.__dataclass_fields__:
                walk(getattr(node, name))
    walk(fn.body)
    return found


# --- the validator ----------------------------------------------------------------------

def _replace_first(fn: MIRFunction, match, change) -> MIRFunction:
    block, index = next((b, i) for b in fn.blocks for i, s in enumerate(b.statements) if match(s))
    stmt = change(block.statements[index])
    return replace(fn, blocks=tuple(
        replace(b, statements=(*b.statements[:index], stmt, *b.statements[index + 1:])) if b is block else b
        for b in fn.blocks))


def _retype(fn: MIRFunction, sid, typ, layouts: MIRFunction) -> MIRFunction:
    """`fn` with slot `sid` retyped, and `layouts`' opaque layout of `typ` added."""
    layout, = (r for r in layouts.records if r.type == typ)
    return replace(fn, slots=tuple(replace(s, type=typ) if s.id == sid else s for s in fn.slots),
                   records=(*fn.records, layout))


def test_validator_rejects_damaged_view_facts(active) -> None:
    fn = _fn(active, "owned_from_view")
    big = _fn(active, "takes")
    param, view, result = _slot(fn, "s").id, _slot(fn, "v").id, fn.blocks[-1].terminator.value
    is_borrow = lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRBorrow)
    is_copy = lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRCopy)
    validate_function(fn)
    # A StrView holder borrowing a BigInt place.
    with pytest.raises(MIRValidationError, match="storage borrow type mismatch"):
        validate_function(_retype(fn, param, BIGINT, big))
    # A copy through a view into owned storage of another family.
    with pytest.raises(MIRValidationError, match="owned leaf copy source mismatch"):
        validate_function(_retype(fn, result, BIGINT, big))
    # A view holder that could write through its loan.
    with pytest.raises(MIRValidationError, match="unsupported reference slot type or form"):
        validate_function(replace(fn, slots=tuple(
            replace(s, readonly=False) if s.id == view else s for s in fn.slots)))
    # A field projected through a view.
    projected = _replace_first(fn, is_copy, lambda s: replace(s, value=replace(s.value, source=MIRPlace(
        view, (MIRDeref(), MIRField(MIRFieldId(STRVIEW, "size"), INT32))))))
    with pytest.raises(MIRValidationError, match="field projection through a view"):
        validate_function(projected)
    # A view holder taking a static literal of another family.
    with pytest.raises(MIRValidationError, match="static literal needs a borrowed holder of its type"):
        validate_function(_replace_first(fn, is_borrow, lambda s: replace(s, value=MIRConstant(b"x"))))
    # A view parameter reseated in the body, or passed by reference.
    explicit = _fn(active, "explicit_view_param")
    view_param = _slot(explicit, "v").id
    reseat = _replace_first(explicit, lambda s: isinstance(s, MIRAssign) and isinstance(s.value, MIRAlias),
                            lambda s: replace(s, target=MIRPlace(view_param), value=MIRAlias(s.target.root)))
    with pytest.raises(MIRValidationError, match="reference parameter reseat"):
        validate_function(reseat)
    with pytest.raises(MIRValidationError, match="view parameter needs a by-value passing"):
        validate_function(replace(explicit, slots=tuple(
            replace(s, passing=ParamPassing.VIEW) if s.id == view_param else s for s in explicit.slots)))
    # A view result claiming a writable borrow.
    with pytest.raises(MIRValidationError, match="unsupported return type or access"):
        validate_function(replace(explicit, borrowed_result=th.THIRBorrowedRecord(STRVIEW, False)))

