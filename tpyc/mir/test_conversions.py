"""The conversions a fragment's value takes on its way to a consumer: its
producer returns the value itself (a place, fresh storage, a static
literal, a borrowed result's holder, a view) and the consumer converts it
-- `materialize` (fresh storage of the full expression's region), `lend`
(a holder an operation reads), `sink` (what a destination's write takes),
`view` (what a view holder takes)."""

import re

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import analyze_call_workspace
from ..thir.testutil import _compile, _entry
from ..typesys import STR, STRVIEW
from .testutil import compiled_definitions
from .coverage import MIRUnsupported
from .collect import call_definitions, dump_codegen_mir, enumerate_body_sources, lower_body
from .emit import Fragment, MIRValue, owned_leaf_shape, view_holder_shape
from .lower import _Lowering
from .nodes import MIRConstant, MIRRecordWriteMode
from ..thir.scalar_leaves import MIRClass
from .value import MIRRole, MIRShape

SOURCE = '''\
from tpy import int32, pure, readonly, Own, String, StrView
from tpy.extern import native


class P:
    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    def __init__(self, name: String, label: str) -> None:
        self.name = name
        self.label = label


class T:
    text: StrView

    def __init__(self, text: StrView) -> None:
        self.text = text


def keep(s: Own[str]) -> int32:
    return len(s)


def takes(s: str) -> int32:
    return len(s)


def vlen(v: StrView) -> int32:
    return len(v)


def adopt(p: Own[P]) -> int32:
    return p.x


def fresh(s: str) -> str:
    return s


def first(t: T) -> StrView:
    return t.text


@pure
@native("pick")
def pick(a: str, b: str) -> str: ...


def owned_argument(s: str) -> int32:
    return keep(s)


def record_argument() -> int32:
    return adopt(P(1))


def lent_fresh(s: str) -> int32:
    return takes(s + "x")


def print_fresh(s: str) -> None:
    print(fresh(s))


def sink_fresh(s: str) -> str:
    x = fresh(s)
    return x


def copy_string(s: String) -> str:
    return s


def lend_string(s: String) -> int32:
    return takes(s)


def view_string(s: String) -> int32:
    v: StrView = s
    return len(v)


def field_passthrough(h: H) -> str:
    t: str = h.name
    return t


def element_passthrough(xs: list[String]) -> str:
    t: str = xs[0]
    return t


def literal_sink() -> int32:
    return keep("abc")


def literal_view() -> int32:
    v: StrView = "abc"
    return len(v)


def field_view(h: H) -> int32:
    v: StrView = h.label
    return len(v)


def view_member(t: T) -> int32:
    v: StrView = t.text
    return len(v)


def fresh_view(s: str) -> int32:
    return vlen(s + "x")


def safe_element_copy(xs: list[int]) -> None:
    i: int32 = 0
    while i < len(xs):
        y: int = xs[i]
        print(y)
        i += 1


def checked_element_borrow(xs: list[str]) -> int32:
    return takes(xs[0])


def borrowed_result_view(a: str, b: str) -> int32:
    return vlen(pick(a, b))


def view_result(t: T) -> int32:
    v: StrView = first(t)
    return len(v)


def scalar_reads(p: P, xs: list[int32], n: int32) -> int32:
    a = n
    b = p.x
    c = xs[n]
    return a + b + c


def make_p(x: int32) -> Own[P]:
    return P(x)


def e_construct() -> int32:
    p = P(1)
    return p.x


def e_temporary_field() -> int32:
    return P(2).x


def e_call_result() -> int32:
    p = make_p(3)
    return p.x


def e_scalar_call(s: str) -> int32:
    return takes(s) + 1


def values_of(d: dict[str, int32]) -> int32:
    d["a"] = 1
    n = 0
    for v in d.values():
        n += v
    return n


def ro_values(d: readonly[dict[str, int32]]) -> int32:
    n = 0
    for v in d.values():
        n += v
    return n


class B:
    def __init__(self, x: int) -> None:
        self.x = x


def lent_b(b: B) -> int:
    return b.x


def lent_temp_operand(c: bool) -> int:
    y = 0
    if c:
        y = lent_b(B(2))
    return y
'''


@pytest.fixture(scope="module")
def unit():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(entry)
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
        dump = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, definitions,
                                compiler.thir_reject_by_node, workspace)
        sources = {s.body.declaration.split("@", 1)[0]: s for s in enumerate_body_sources(
            entry.ast, entry.analyzer, ctx, entry.name, compiler.thir_reject_by_node)}
    return compiler, definitions, workspace, sources, dump


@pytest.fixture(scope="module")
def dumps(unit) -> dict[str, list[str]]:
    bodies: dict[str, list[str]] = {}
    for line in unit[4].splitlines():
        if line.startswith("fn "):
            name = line.split("::", 1)[1].split("@", 1)[0]
            bodies[name] = [line]
        elif bodies:
            # The module's own callees by their bare names.
            bodies[name].append(re.sub(r" @ \d+:\d+$", "", re.sub(r"call \w+::", "call ", line.strip())))
    return bodies


def _statements(lines: list[str]) -> list[str]:
    """The body's statements, its blocks' labels and jumps left out."""
    end = next(i for i, line in enumerate(lines) if line.startswith("liveness"))
    return [line for line in lines[:end] if re.match(r"(%\d+ = |print |return)", line)]


# --- fresh values ----------------------------------------------------------------

def test_an_owning_owned_leaf_argument_is_storage_the_call_takes(dumps) -> None:
    lines = dumps["owned_argument"]
    # The callee's own copy: storage of the full expression's region, passed as it is.
    assert "%1: str owned-storage temporary duration=r1 residence=r1" in lines
    assert "%1 = copy (*%0) may-raise [initialize_region]" in lines
    assert "%2 = call keep(%1) [reader, may-raise]" in lines
    assert not any(" = borrow %1" in line for line in lines)


def test_a_record_temporary_is_storage_the_call_takes(dumps) -> None:
    lines = dumps["record_argument"]
    assert "%1: P owned-storage temporary duration=r1 residence=r1" in lines
    assert "%1 = construct (%0) [initialize_region]" in lines
    assert any(line.startswith("%2 = call adopt(%1) ") for line in lines)


def test_a_fresh_operand_is_materialized_then_lent(dumps) -> None:
    lines = dumps["lent_fresh"]
    assert "%3: String owned-storage temporary duration=r1 residence=r1" in lines
    body = lines[lines.index("bb1:") + 1:]
    start = body.index("%3 = op + (%1, %2) may-raise [initialize_region]")
    # The storage, then a holder lending it, then the view the parameter takes.
    assert body[start + 1:start + 3] == ["%4 = borrow %3", "%5 = alias %4"]


def test_a_fresh_value_printed_is_materialized_and_lent_but_sunk_directly(dumps) -> None:
    # Printed: an operand, so storage of the full expression lent through a holder.
    assert _statements(dumps["print_fresh"]) == [
        "%1 = borrow (*%0)", "%2 = call fresh(%1) [reader, may-raise] [initialize_region]",
        "%3 = borrow %2", "print (%3)", "return"]
    # Sunk: the call initializes the local itself.
    assert _statements(dumps["sink_fresh"]) == [
        "%1 = borrow (*%0)", "%2 = call fresh(%1) [reader, may-raise] [initialize_once]", "return %2"]


def test_a_fresh_value_viewed_is_materialized_lent_and_aliased(dumps) -> None:
    statements = _statements(dumps["fresh_view"])
    start = statements.index("%3 = op + (%1, %2) may-raise [initialize_region]")
    assert statements[start + 1:start + 4] == ["%4 = borrow %3", "%5 = alias %4", "%6 = call vlen(%5) "
                                               "[reader, may-raise]"]


def test_lending_fresh_storage_before_it_is_materialized_is_a_lowering_defect() -> None:
    root = Fragment()
    f = root.fragment()
    f.value = MIRValue(MIRConstant("x"), owned_leaf_shape(STR, MIRRole.STORAGE))
    with pytest.raises(AssertionError, match="fresh storage is lent once materialized"):
        root.fragment().lend(f)


def test_materialize_registers_its_node_and_refuses_an_unregistered_one() -> None:
    root = Fragment()
    f = root.fragment()
    value = MIRValue(MIRConstant("x"), owned_leaf_shape(STR, MIRRole.STORAGE))
    with pytest.raises(AssertionError, match="needs a full expression"):
        f.materialize(value, "node")
    root.active_temporaries = []
    with pytest.raises(AssertionError, match="unregistered materialized storage"):
        f.materialize(value)
    storage = f.materialize(value, "node")
    assert root.active_temporaries == ["node"] and f.holds_region_storage()
    (write,) = f.head.statements
    assert write.target.root == storage and write.storage_write.mode is MIRRecordWriteMode.INITIALIZE_REGION
    # A full expression holding region storage it does not say it needs is a lowering defect.
    with pytest.raises(AssertionError, match="region storage without a region need"):
        _Lowering.whole(None, f, None)
    assert _Lowering.whole(None, f, None, True).needs_region


# --- a String read as str (a passthrough) ------------------------------------------

def test_a_string_read_as_str_is_copied_lent_and_viewed_in_place(dumps) -> None:
    assert _statements(dumps["copy_string"]) == ["%1 = copy (*%0) may-raise [initialize_once]", "return %1"]
    # Lent through a holder typed by the family's view.
    assert "%1: StrView readonly-ref temporary residence=r0" in dumps["lend_string"]
    assert _statements(dumps["lend_string"])[:2] == ["%1 = borrow (*%0)", "%2 = call takes(%1) "
                                                     "[reader, may-raise]"]
    assert _statements(dumps["view_string"])[0] == "%1 = borrow (*%0)"


def test_a_lent_string_is_a_str_value_held_through_its_view(unit, monkeypatch) -> None:
    seen = []
    operand = _Lowering.operand

    def spy(self, expr, v):
        g = operand(self, expr, v)
        seen.append((g.shape_of(), g.root.records[g.value.value].shape))
        return g
    monkeypatch.setattr(_Lowering, "operand", spy)
    compiler, definitions, workspace, sources, _ = unit
    with activate_compiler(compiler):
        lower_body(sources["lend_string"], definitions, workspace, fresh=True)
    # The expression is a `str`; only its holder slot is typed by the view.
    (value, holder), = seen
    assert value.type == STR and holder == view_holder_shape(STRVIEW)


def test_a_string_field_and_element_read_as_str_are_one_copy_of_the_place(dumps) -> None:
    assert _statements(dumps["field_passthrough"]) == [
        "%1 = copy (*%0).__main__.H::name may-raise [initialize_once]", "return %1"]
    assert _statements(dumps["element_passthrough"]) == [
        "%1 = 0", "%2 = copy (*%0)[elements] may-raise [initialize_once]", "return %2"]


# --- static literals, views and borrowed results ----------------------------------------

def test_a_static_literal_is_sunk_and_viewed_as_the_constant(dumps) -> None:
    assert _statements(dumps["literal_sink"])[0] == "%0 = 'abc' [initialize_region]"
    assert _statements(dumps["literal_view"])[0] == "%0 = 'abc'"


def test_a_field_viewed_is_one_borrow(dumps) -> None:
    # An owned-leaf field and a view member alike: the view holder borrows the field.
    assert _statements(dumps["field_view"])[0] == "%1 = borrow (*%0).__main__.H::label"
    assert _statements(dumps["view_member"])[0] == "%1 = borrow (*%0).__main__.T::text"


def test_a_borrowed_owned_leaf_result_is_viewed_through_its_holder(dumps) -> None:
    statements = _statements(dumps["borrowed_result_view"])
    assert statements[2].startswith("%4 = call stub __main__.pick[str, str](%2, %3)")
    assert statements[3:5] == ["%5 = alias %4", "%6 = call vlen(%5) [reader, may-raise]"]


def test_a_view_result_is_passed_as_it_is(dumps) -> None:
    statements = _statements(dumps["view_result"])
    assert statements[0].startswith("%1 = call first(%0) ")
    assert statements[1].startswith("%2 = call stub tpy._builtins._funcs.len[Sized](%1) ")


# --- elements and scalar reads -----------------------------------------------------

def test_an_element_copy_raises_though_its_index_is_proven(dumps) -> None:
    statements = _statements(dumps["safe_element_copy"])
    assert "%7 = copy (*%0)[elements] may-raise [initialize_region]" in statements


def test_a_borrow_of_an_unproven_element_raises(dumps) -> None:
    assert "%2 = borrow (*%0)[elements] may-raise" in _statements(dumps["checked_element_borrow"])


def test_scalar_reads_of_a_name_a_field_and_an_element(dumps) -> None:
    statements = _statements(dumps["scalar_reads"])
    assert statements[:7] == ["%3 = read %2", "%4 = read %3", "%5 = read (*%0).__main__.P::x", "%6 = read %5",
                              "%7 = read %2", "%8 = read (*%1)[elements] may-raise", "%9 = read %8"]


def test_a_scalar_read_lowers_its_place_once(unit, monkeypatch) -> None:
    seen = []
    place = _Lowering.place

    def spy(self, expr):
        f = place(self, expr)
        value = f.value.value
        steps = tuple(type(p).__name__ for p in value.projections)
        seen.append((type(expr).__name__, value.root.index, steps, f.value.raises))
        return f
    monkeypatch.setattr(_Lowering, "place", spy)
    compiler, definitions, workspace, sources, _ = unit
    with activate_compiler(compiler):
        lower_body(sources["scalar_reads"], definitions, workspace, fresh=True)
    # The name, the field and the element are each one place; only the element's read may raise.
    assert ("THIRFieldAccess", 0, ("MIRDeref", "MIRField"), False) in seen
    assert ("THIRSubscript", 1, ("MIRDeref", "MIRContainerElements"), True) in seen
    assert ("THIRName", 2, (), False) in seen
    assert {raises for kind, *_, raises in seen if kind != "THIRSubscript"} == {False}


def test_a_container_operand_refuses_an_element_place(unit, monkeypatch) -> None:
    refused = []
    element_access = _Lowering.element_access

    def spy(self, expr):
        # An element place lent whole would raise nothing: its bounds exit would be dropped.
        with pytest.raises(MIRUnsupported, match="container source needs a place"):
            self.container_place(expr)
        refused.append(expr)
        return element_access(self, expr)
    monkeypatch.setattr(_Lowering, "element_access", spy)
    compiler, definitions, workspace, sources, _ = unit
    with activate_compiler(compiler):
        lower_body(sources["scalar_reads"], definitions, workspace, fresh=True)
    assert len(refused) == 1


# --- calls state their result's shape ------------------------------------------------

def _stated(unit, monkeypatch, body: str) -> list[tuple[str, object, MIRShape]]:
    """Each call's and construct's value as built while `body` lowers: its kind, summary and stated shape."""
    stated = []
    call_value, construct = _Lowering.call_value, _Lowering.construct

    def call_spy(self, f, node, summary, *rest):
        g = call_value(self, f, node, summary, *rest)
        stated.append(("call", summary, g.value.shape))
        return g

    def construct_spy(self, *args):
        v = construct(self, *args)
        stated.append(("construct", None, v.shape))
        return v
    monkeypatch.setattr(_Lowering, "call_value", call_spy)
    monkeypatch.setattr(_Lowering, "construct", construct_spy)
    compiler, definitions, workspace, sources, _ = unit
    with activate_compiler(compiler):
        lower_body(sources[body], definitions, workspace, fresh=True)
    return stated


def test_an_rvalue_without_a_stated_shape_is_a_lowering_defect() -> None:
    with pytest.raises(AssertionError, match="an rvalue states its shape"):
        MIRValue(MIRConstant(1))


def test_a_construct_states_record_storage(unit, monkeypatch, dumps) -> None:
    for body, write in (("e_construct", "initialize_once"), ("e_temporary_field", "initialize_region")):
        (kind, _, shape), = _stated(unit, monkeypatch, body)
        assert kind == "construct" and shape.cls is MIRClass.RECORD and shape.is_storage, body
        assert any(line.startswith("%1 = construct (%0) [" + write) for line in _statements(dumps[body])), body


def test_a_call_states_its_result_shape(unit, monkeypatch, dumps) -> None:
    # An `Own[R]` result is record storage the caller receives.
    (_, _, shape), = _stated(unit, monkeypatch, "e_call_result")
    assert shape.cls is MIRClass.RECORD and shape.is_storage and str(shape.type) == "P"
    assert any(line.startswith("%1 = call make_p(%0) ") and line.endswith("[initialize_once]")
               for line in _statements(dumps["e_call_result"]))
    # A scalar result is a leaf, read into the slot its consumer holds it in.
    (_, _, shape), = (s for s in _stated(unit, monkeypatch, "e_scalar_call") if s[1].callee.identity.name == "takes")
    assert shape.is_scalar and str(shape.type) == "int32"
    assert any(line.startswith("%2 = call takes(%1) ") for line in _statements(dumps["e_scalar_call"]))


def test_a_method_view_result_follows_its_receiver_place(unit, monkeypatch) -> None:
    def values(body):
        return next((summary, shape) for kind, summary, shape in _stated(unit, monkeypatch, body)
                    if kind == "call" and summary.callee.identity.qualified_name.endswith(".values"))
    # A mutable receiver place: the view holder is mutable; a readonly one would make it readonly.
    summary, shape = values("values_of")
    assert shape.cls is MIRClass.CONTAINER_VIEW and not shape.readonly
    assert summary.result_at(True).readonly and not summary.result_at(False).readonly
    # A view whose elements are readonly stays readonly off a mutable receiver.
    summary, shape = values("ro_values")
    assert shape.readonly and summary.result_at(False).readonly


# --- a lent argument temporary's operands ------------------------------------------

def test_a_lent_temporary_operand_ends_with_the_initialization(dumps) -> None:
    lines = dumps["lent_temp_operand"]
    regions = {m[1]: (m[2], m[3]) for m in map(re.compile(r"region (r\d+) parent=(\w+) entry=(bb\d+)").match, lines)
               if m}
    operand = next(re.match(r"(%\d+): int owned-storage temporary duration=(r\d+)", line) for line in lines
                   if re.match(r"%\d+: int owned-storage temporary duration=r", line))
    storage = next(re.match(r"(%\d+): B owned-storage local duration=(r\d+)", line) for line in lines
                   if ": B owned-storage local" in line)
    # The operand's region is a child of the region of the statement the temporary is initialized at.
    parent, entry = regions[operand[2]]
    assert parent == storage[2]
    # It holds exactly the initialization: the operand, its holder, the construct; then it ends.
    block = lines[lines.index(entry + ":") + 1:]
    assert [re.sub(r"%\d+", "%", line) for line in block[:3]] == [
        "% = 2 [initialize_region]", "% = borrow %", "% = construct (%) [initialize_region]"]
    assert block[2].startswith(storage[1] + " = construct") and block[3].startswith("goto ")
    assert f"{entry} edge 0: {operand[2]} {operand[1]}" in lines
