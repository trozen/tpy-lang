"""Callee facts a call carries: a user callee's signature passings, and a
stub callee's declared identity, signature and contract."""

from collections.abc import Iterator
from dataclasses import replace

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import FLOAT, INT32, STR, NominalType, Representation
from . import nodes as th
from .dump import _callee_lines, _function_lines
from .testutil import _compile, _entry
from .validate import THIRValidationError, _iter_children, validate_function

SOURCE = """\
import math
from tpy import int32, int64, pure, StrView
from tpy.extern import native

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def bump(c: Cell, label: str, n: int32) -> int32:
    c.value += n
    return n

def peek(c: Cell, label: str, n: int32) -> int32:
    return c.value + n

@native("probe_clock", transient=True)
def probe_clock() -> float: ...

@pure
@native("probe_pure")
def probe_pure(x: float) -> float: ...

@native("probe_plain")
def probe_plain(c: Cell) -> int32: ...

@native("probe_view")
def probe_view(n: int32) -> StrView: ...

def run(c: Cell, f: float, a: int64) -> None:
    u = bump(c, "x", 1)
    p = peek(c, "y", 2)
    n = math.log10(f)
    ch = chr(u)
    i = int(f)
    s1 = str(u)
    s2 = str(f)
    k = int32(a)
    t = probe_clock()
    q = probe_pure(f)
    r = probe_plain(c)
    v = probe_view(u)
    print(u, p, n, ch, i, s1, s2, k, t, q, r, v)
"""


def walk(node: th.THIRNode) -> Iterator[th.THIRNode]:
    yield node
    for child in _iter_children(node):
        yield from walk(child)


def calls(fn: th.THIRFunction) -> dict[str, th.THIRCall]:
    found = [node for stmt in fn.body for node in walk(stmt) if isinstance(node, th.THIRCall)]
    return {node.callee: node for node in found if node.callee != "str"} | {
        f"str{i}": node for i, node in enumerate(n for n in found if n.callee == "str")}


@pytest.fixture(scope="module")
def lowered() -> tuple[dict[str, th.THIRFunction], object, object]:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    return {fn.name: fn for fn in ctx.thir_functions.values()}, entry, compiler


def test_user_callee_passings_match_the_definition(lowered) -> None:
    functions, _, _ = lowered
    nodes = calls(functions["run"])
    for name in ("bump", "peek"):
        fact = nodes[name].resolved_callee
        assert nodes[name].stub_callee is None
        assert fact == functions[name].resolved_callee
        assert fact.signature.passings == tuple(p.passing for p in functions[name].params)
        assert fact.signature.return_representation is Representation.STORAGE
    # The const verdict splits the record parameter; str passes as a view.
    assert nodes["bump"].resolved_callee.signature.passings == (
        ParamPassing.MUT_REF, ParamPassing.VIEW, ParamPassing.VALUE)
    assert nodes["peek"].resolved_callee.signature.passings == (
        ParamPassing.CONST_REF, ParamPassing.VIEW, ParamPassing.VALUE)


def test_native_template_and_constructor_stubs(lowered) -> None:
    functions, _, _ = lowered
    nodes = calls(functions["run"])
    for name in ("log10", "chr", "int", "probe_clock", "probe_pure", "probe_plain", "probe_view", "str0", "str1"):
        assert nodes[name].stub_callee is not None, name
        assert nodes[name].resolved_callee is None, name
    log10 = nodes["log10"].stub_callee
    assert log10.identity == th.THIRStubIdentity("math.log10", (FLOAT,))
    assert log10.signature.passings == (ParamPassing.VALUE,)
    assert log10.signature.return_type == FLOAT
    assert log10.signature.return_representation is Representation.STORAGE
    assert log10.contract is None and log10.readonly == (False,)
    chr_ = nodes["chr"].stub_callee
    assert nodes["chr"].cpp_template is not None
    assert chr_.identity.qualified_name.endswith(".chr") and chr_.identity.param_types == (INT32,)
    assert chr_.contract is th.THIRStubContract.PURE and chr_.readonly == (True,)
    # An initializer's result is the type it initializes, not its `None`.
    ctor = nodes["int"].stub_callee
    assert nodes["int"].constructs
    assert ctor.identity == th.THIRStubIdentity("builtins.int.__init__", (FLOAT,))
    assert isinstance(ctor.signature.return_type, NominalType)
    assert ctor.signature.return_type.qualified_name() == "builtins.int"
    assert ctor.signature.return_representation is Representation.STORAGE


def test_declared_contracts(lowered) -> None:
    functions, entry, _ = lowered
    nodes = calls(functions["run"])
    assert nodes["probe_clock"].stub_callee.contract is th.THIRStubContract.TRANSIENT
    assert nodes["probe_pure"].stub_callee.contract is th.THIRStubContract.PURE
    plain = nodes["probe_plain"].stub_callee
    assert plain.contract is None
    # An unmarked stub declares no const verdict for its record parameter.
    assert plain.readonly == (False,) and plain.signature.passings == (ParamPassing.MUT_REF,)
    registry = entry.analyzer.registry
    clock, = registry.get_function("probe_clock")
    pure_fi, = registry.get_function("probe_pure")
    plain_fi, = registry.get_function("probe_plain")
    assert clock.is_transient and not clock.is_pure
    # @pure implies the transient promise; the stronger contract is what THIR publishes.
    assert pure_fi.is_pure and not pure_fi.is_transient
    assert not plain_fi.is_pure and not plain_fi.is_transient


def test_overloads_of_one_stub_keep_distinct_identities(lowered) -> None:
    functions, _, _ = lowered
    nodes = calls(functions["run"])
    first, second = nodes["str0"].stub_callee, nodes["str1"].stub_callee
    assert first.identity.qualified_name == second.identity.qualified_name == "builtins.str.__init__"
    assert first.identity.param_types == (INT32,) and second.identity.param_types == (FLOAT,)
    assert first.identity != second.identity
    assert first.signature.return_type == second.signature.return_type == STR


def test_view_result_reports_view_representation(lowered) -> None:
    functions, _, _ = lowered
    view = calls(functions["run"])["probe_view"].stub_callee
    assert view.signature.return_representation is Representation.VIEW


def test_open_type_parameter_is_unpublished(lowered) -> None:
    functions, _, _ = lowered
    # `int32(a)` resolves the generic `__init__[T]` overload.
    node = calls(functions["run"])["int32"]
    assert node.constructs and node.stub_callee is None and node.resolved_callee is None


def test_validator_rejects_conflicting_and_malformed_callee_facts(lowered) -> None:
    functions, _, _ = lowered
    run = functions["run"]
    nodes = calls(run)
    user, stub = nodes["bump"], nodes["log10"]

    def check(call: th.THIRCall) -> None:
        validate_function(replace(run, body=(th.THIRExprStmt(call),)))

    check(stub)
    with pytest.raises(THIRValidationError, match="both a resolved and a stub callee"):
        check(replace(user, stub_callee=stub.stub_callee))
    short = replace(user.resolved_callee.signature, passings=(ParamPassing.VALUE,))
    with pytest.raises(THIRValidationError, match="invalid resolved callee"):
        check(replace(user, resolved_callee=replace(user.resolved_callee, signature=short)))
    fact = stub.stub_callee
    for broken in (
        replace(fact, signature=replace(fact.signature, passings=())),
        replace(fact, signature=replace(fact.signature, passings=None)),
        replace(fact, signature=replace(fact.signature, return_representation=Representation.VIEW)),
        replace(fact, identity=replace(fact.identity, param_types=())),
        replace(fact, readonly=()),
    ):
        with pytest.raises(THIRValidationError, match="invalid stub callee"):
            check(replace(stub, stub_callee=broken))
    with pytest.raises(THIRValidationError, match="stub callee on incompatible call"):
        check(replace(stub, args=()))
    # A definition whose published passings disagree with its parameters.
    bump = functions["bump"]
    wrong = replace(bump.resolved_callee.signature, passings=(ParamPassing.CONST_REF,) * 3)
    with pytest.raises(THIRValidationError, match="resolved callee disagrees"):
        validate_function(replace(bump, resolved_callee=replace(bump.resolved_callee, signature=wrong)))


def test_dump_lists_callee_facts_after_the_body(lowered) -> None:
    functions, _, _ = lowered
    run = functions["run"]
    # The statement render stays the argument lowering the sweep tables pin.
    assert not any("callee " in line or "stub " in line for line in _function_lines(run))
    lines = _callee_lines(run)
    assert lines[0] == "  signature(Ref[Cell]: mut_ref, float: value, int64: value) -> reference"
    assert "    [0] bump: callee main.bump(Ref[Cell]: mut_ref, str: view, int32: value) -> storage" in lines
    assert "    [2] log10: stub math.log10(float: value) -> storage, none" in lines
    assert any(line.endswith("probe_clock: stub __main__.probe_clock() -> storage, transient") for line in lines)


def test_audited_stub_contracts() -> None:
    source = """\
import math
import time
def run(xs: list[int], f: float) -> None:
    n = len(xs)
    t = time.time()
    l = math.log(f)
    i = int(f)
    print(n, t, l, i)
"""
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    run = next(fn for fn in ctx.thir_functions.values() if fn.name == "run")
    contracts = {name: node.stub_callee.contract for name, node in calls(run).items() if name != "print"}
    assert contracts == {"len": th.THIRStubContract.PURE, "time": th.THIRStubContract.TRANSIENT,
                         "log": th.THIRStubContract.PURE, "int": th.THIRStubContract.PURE}
