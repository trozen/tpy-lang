"""Facts THIR publishes for native containers: the method stub callee of a
native builtin method call or subscript write (the instantiated receiver as
parameter 0), and the container fact of parameters, owned locals and
loops. Analysis only -- no render reads them."""

from collections.abc import Iterator
from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..type_def_registry import ParamPassing
from ..typesys import INT32, STR, STRVIEW, NominalType, OwnType, Representation, VoidType
from . import nodes as th
from .testutil import _compile, _entry
from .validate import THIRValidationError, _iter_children, validate_function

SOURCE = """\
from tpy import int32, Own, Span, StrView, readonly

class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def bump(self) -> None:
        self.x += 1

class Named:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Ordered:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __lt__(self, other: Ordered) -> bool:
        return self.x < other.x

def methods(xs: list[int32], d: dict[str, int32], ps: list[Point], os: list[Ordered], ys: list[int32]) -> int32:
    xs.append(1)
    a = xs.pop()
    b = xs.pop(0)
    xs.sort()
    os.sort()
    xs.extend(ys)
    g = d.get("k", 0)
    s = d.setdefault("k", 0)
    n = 0
    for v in d.values():
        n += v
    ps.append(Point(1))
    ps[0].bump()
    return a + b + g + s + n

def writes(xs: list[int32], d: dict[str, int32], ps: list[Point], k: str) -> None:
    xs[0] = 1
    xs[1] += 2
    d[k] = 3
    ps[0] = Point(2)

def facts(names: list[str], d: dict[str, int32], named: list[Named], sp: Span[int32], rsp: Span[readonly[int32]],
          own: Own[list[int32]], views: list[StrView], ordered: list[Ordered], points: set[int32],
          nested: list[list[int32]], table: dict[str, Point]) -> int32:
    xs = [1, 2, 3]
    ys: list[int32] = [len(names)]
    alias = ys
    t = sp[0] + rsp[0]
    for k in d:
        t += d[k]
    s = ys[0:1]
    return t + xs[0] + len(alias) + s[0] + len(own)

class Bag:
    items: list[int32]
    def __init__(self) -> None:
        self.items = []

def fields(b: Bag, ps: list[Point]) -> int32:
    t = ps[0].x
    for v in b.items:
        t += v
    return t

def alias_field(b: Bag) -> None:
    b.items.append(2)
    x = b.items
    x.append(1)

def main() -> None:
    xs: list[int32] = [3, 1, 2]
    ps = [Point(1)]
    os = [Ordered(2), Ordered(1)]
    d = {"k": 1}
    print(methods(xs, d, ps, os, [4]))
    writes(xs, d, ps, "k")

main()
"""


def walk(node: th.THIRNode) -> Iterator[th.THIRNode]:
    yield node
    for child in _iter_children(node):
        yield from walk(child)


def nodes(fn: th.THIRFunction, kind: type) -> list:
    return [node for stmt in fn.body for node in walk(stmt) if isinstance(node, kind)]


# Per test: the borrowing-view facts are the compiled stubs', cleared between tests.
@pytest.fixture
def compiled():
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return compiler, {fn.name: fn for fn in ctx.thir_functions.values()}


@pytest.fixture
def lowered(compiled) -> Iterator[dict[str, th.THIRFunction]]:
    # The element rule reads the program's record TypeDefs.
    compiler, functions = compiled
    with activate_compiler(compiler):
        yield functions


def _list(element) -> NominalType:
    return NominalType("list", (element,), _module_qname="builtins.list")


def _stubs(fn: th.THIRFunction) -> dict[str, list[th.THIRStubCallee | None]]:
    found: dict[str, list] = {}
    for call in nodes(fn, th.THIRMethodCall):
        name = call.stub_callee.identity.qualified_name if call.stub_callee else call.method_cpp
        found.setdefault(name, []).append(call.stub_callee)
    return found


def test_a_method_stub_binds_the_instantiated_receiver_first(lowered) -> None:
    stubs = _stubs(lowered["methods"])
    xs = _list(INT32)
    append, = (s for s in stubs["builtins.list.append"] if s.signature.param_types[0] == xs)
    assert append.identity == th.THIRStubIdentity("builtins.list.append", (xs, OwnType(INT32)))
    assert append.signature.passings == (ParamPassing.MUT_REF, ParamPassing.VALUE)
    assert append.signature.return_type == VoidType() and append.readonly == (False, False)
    assert append.receiver and not append.mutates_elements and append.contract is None
    assert append.bound_arguments == ()
    # The two `pop` overloads are two identities.
    nullary, indexed = stubs["builtins.list.pop"]
    assert nullary.identity.param_types == (xs,) and indexed.identity.param_types == (xs, INT32)
    assert nullary.signature.return_representation is Representation.STORAGE
    # The element type a record list instantiates is part of the identity.
    record, = (s for s in stubs["builtins.list.append"] if s is not append)
    assert record.identity.param_types[0] != xs
    assert record.signature.passings == (ParamPassing.MUT_REF, ParamPassing.OWN)


def test_method_stubs_carry_their_receiver_and_bound_facts(lowered) -> None:
    stubs = _stubs(lowered["methods"])
    ints, records = stubs["builtins.list.sort"]
    # A protocol-bounded type parameter names the receiver's type argument it binds.
    assert ints.bound_arguments == (INT32,)
    assert records.bound_arguments[0].qualified_name() == "__main__.Ordered"
    get, = stubs["builtins.dict.get"]
    assert get.contract is th.THIRStubContract.PURE and get.readonly == (True, True, True)
    assert get.signature.passings == (ParamPassing.CONST_REF, ParamPassing.VIEW, ParamPassing.VALUE)
    setdefault, = stubs["builtins.dict.setdefault"]
    # A key passed as a view cannot be written through.
    assert setdefault.readonly == (False, True, False) and setdefault.contract is None
    # The mutable clone of an @auto_readonly view accessor is pure, with a mutable receiver.
    values, = stubs["builtins.dict.values"]
    assert values.contract is th.THIRStubContract.PURE and values.readonly == (False,)
    assert values.signature.passings == (ParamPassing.MUT_REF,)
    assert values.signature.return_representation is Representation.VIEW
    extend, = stubs["builtins.list.extend"]
    assert extend.signature.param_types[1].is_protocol
    # A user record's method has no stub.
    assert stubs["bump"] == [None]


def test_a_subscript_write_publishes_the_setitem_overload_its_index_selects(lowered) -> None:
    items = nodes(lowered["writes"], th.THIRSetItem)
    assert len(items) == 4 and all(item.stub_callee is not None for item in items)
    literal, augmented, keyed, record = (item.stub_callee for item in items)
    # An int literal index selects the int32 overload, never the slice ones.
    assert literal.identity == th.THIRStubIdentity("builtins.list.__setitem__",
                                                   (_list(INT32), INT32, OwnType(INT32)))
    assert literal.mutates_elements and literal.receiver and literal.contract is None
    assert augmented == literal
    assert keyed.identity.param_types[1] == STR and keyed.readonly == (False, True, False)
    assert keyed.signature.passings == (ParamPassing.MUT_REF, ParamPassing.VIEW, ParamPassing.VALUE)
    assert record.signature.passings == (ParamPassing.MUT_REF, ParamPassing.VALUE, ParamPassing.OWN)


def test_owned_container_locals_and_view_loops(lowered) -> None:
    fn = lowered["facts"]
    decls = {d.name: d for d in nodes(fn, th.THIRVarDecl)}
    assert decls["xs"].form is not th.Form.BORROW and decls["alias"].form is th.Form.BORROW
    loop, = nodes(fn, th.THIRForEach)
    # The loop variable views the str key it binds.
    assert loop.elem_type == STRVIEW


def _replace_node(fn: th.THIRFunction, old: th.THIRNode, new: th.THIRNode) -> th.THIRFunction:
    def rebuild(node):
        if node is old:
            return new
        if isinstance(node, tuple):
            return tuple(rebuild(n) for n in node)
        if not isinstance(node, th.THIRNode):
            return node
        changes = {f: rebuild(getattr(node, f)) for f in node.__dataclass_fields__
                   if isinstance(getattr(node, f), (th.THIRNode, tuple))}
        changes = {k: v for k, v in changes.items() if v is not getattr(node, k)}
        return replace(node, **changes) if changes else node
    return replace(fn, body=rebuild(fn.body))


def _damaged_method_stubs(stub: th.THIRStubCallee) -> list[tuple[th.THIRStubCallee, str]]:
    signature = stub.signature
    other = (_list(STR), *signature.param_types[1:])
    return [
        (replace(stub, receiver=False), "invalid stub callee"),
        (replace(stub, mutates_elements=1), "invalid stub callee"),
        (replace(stub, bound_arguments=("int32",)), "invalid stub callee"),
        (replace(stub, identity=replace(stub.identity, param_types=other),
                 signature=replace(signature, param_types=other)),
         "method stub callee disagrees with its receiver"),
        (replace(stub, signature=replace(signature, passings=(ParamPassing.CONST_REF, *signature.passings[1:]))),
         "method stub callee disagrees with its receiver"),
        (replace(stub, identity=replace(stub.identity, param_types=signature.param_types[:1]),
                 signature=replace(signature, param_types=signature.param_types[:1],
                                   passings=signature.passings[:1]),
                 readonly=stub.readonly[:1]),
         "method stub callee disagrees with its receiver"),
    ]


def test_validator_rejects_inconsistent_method_stubs(lowered) -> None:
    fn = lowered["methods"]
    validate_function(fn)
    call = next(c for c in nodes(fn, th.THIRMethodCall)
                if c.stub_callee is not None and c.stub_callee.identity.qualified_name == "builtins.list.append")
    for damaged, reason in _damaged_method_stubs(call.stub_callee):
        with pytest.raises(THIRValidationError, match=reason):
            validate_function(_replace_node(fn, call, replace(call, stub_callee=damaged)))
    writes = lowered["writes"]
    item = nodes(writes, th.THIRSetItem)[0]
    for damaged, reason in _damaged_method_stubs(item.stub_callee)[:3]:
        with pytest.raises(THIRValidationError, match=reason):
            validate_function(_replace_node(writes, item, replace(item, stub_callee=damaged)))
    # The receiver of a subscript write is the subscript's.
    with pytest.raises(THIRValidationError, match="setitem stub callee needs a subscript target"):
        validate_function(_replace_node(writes, item, replace(item, target=item.target.receiver)))


def test_validator_rejects_a_method_stub_on_a_free_call(lowered) -> None:
    fn = lowered["facts"]
    call = next(c for c in nodes(fn, th.THIRCall) if c.stub_callee is not None)
    for damaged in (replace(call.stub_callee, receiver=True), replace(call.stub_callee, mutates_elements=True),
                    replace(call.stub_callee, bound_arguments=(INT32,)),
                    replace(call.stub_callee, constructs_result=1)):
        with pytest.raises(THIRValidationError, match="invalid stub callee"):
            validate_function(_replace_node(fn, call, replace(call, stub_callee=damaged)))


def test_validator_rejects_a_constructed_result_on_a_method_stub(lowered) -> None:
    # Only an initializer builds its result; a method stub's result is its own.
    fn = lowered["methods"]
    call = next(c for c in nodes(fn, th.THIRMethodCall) if c.stub_callee is not None)
    damaged = replace(call.stub_callee, constructs_result=True)
    with pytest.raises(THIRValidationError, match="invalid stub callee"):
        validate_function(_replace_node(fn, call, replace(call, stub_callee=damaged)))


def test_container_fields_and_element_fields_publish_identities(lowered) -> None:
    fn = lowered["fields"]
    accesses = {a.field_identity.name: a for a in nodes(fn, th.THIRFieldAccess)}
    # A field of a container element is reached in place, like a record field.
    x = accesses["x"]
    assert isinstance(x.receiver, th.THIRSubscript) and x.field_identity.type == INT32
    # A container field is a member place of its record.
    items = accesses["items"]
    assert items.field_identity.type == _list(INT32)
    loop, = nodes(fn, th.THIRForEach)
    assert loop.iterable is items


def test_validator_rejects_inconsistent_field_facts(lowered) -> None:
    fn = lowered["fields"]
    validate_function(fn)
    accesses = {a.field_identity.name: a for a in nodes(fn, th.THIRFieldAccess)}
    x = accesses["x"]
    subscript = x.receiver
    # An element field needs a container element receiver.
    not_elements = replace(subscript, receiver=replace(subscript.receiver, result_type=STR))
    with pytest.raises(THIRValidationError, match="field identity disagrees with its access"):
        validate_function(_replace_node(fn, x, replace(x, receiver=not_elements)))


def test_a_dict_view_loop_iterates_the_view_a_stub_returns(lowered) -> None:
    loop, = nodes(lowered["methods"], th.THIRForEach)
    view = loop.iterable.result_type
    assert isinstance(loop.iterable, th.THIRMethodCall) and view.qualified_name() == "builtins.dict_values"
    assert loop.elem_type == INT32


def test_a_local_alias_of_a_container_field_borrows_it(lowered) -> None:
    fn = lowered["alias_field"]
    validate_function(fn)
    decl, = (d for d in nodes(fn, th.THIRVarDecl) if d.name == "x")
    assert isinstance(decl.init, th.THIRFieldAccess) and decl.form is th.Form.BORROW
