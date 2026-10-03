"""The callee fact THIR publishes for a user record's ordinary instance
method: one identity (module, name, owning record) and one signature whose
parameter 0 is the receiver, the same value at the definition and at every
call that statically resolves to it. Analysis only -- no render reads it."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..type_def_registry import ParamPassing
from ..typesys import INT32, STR, NominalType, RefType, VoidType
from . import nodes as th
from .dump import dump_codegen_thir
from .lower import iter_module_callables
from .lower.callables import method_callee
from .test_method_stubs import _replace_node, nodes
from .testutil import _compile, _entry
from .validate import THIRValidationError, validate_function

SOURCE = """\
from tpy import int32

class Counter:
    n: int32
    name: str
    items: list[int32]
    def __init__(self, name: str) -> None:
        self.n = 0
        self.name = name
        self.items = []
    def bump(self) -> None:
        self.n += 1
    def total(self) -> int32:
        t = 0
        for x in self.items:
            t += x
        return t
    def push(self, v: int32) -> None:
        self.items.append(v)
    def merge(self, other: Counter, label: str) -> None:
        self.n += other.n
        self.name = label
    def bump_twice(self) -> None:
        self.bump()
        self.bump()

class Gauge:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def bump(self) -> None:
        self.n += 2

def use(c: Counter, d: Counter, g: Gauge, xs: list[int32]) -> int32:
    c.bump()
    c.push(3)
    c.merge(d, "m")
    g.bump()
    xs.append(1)
    return c.total()

def free(n: int32) -> int32:
    return n

def main() -> None:
    c = Counter("c")
    c.bump_twice()
    n = free(2)
    print(use(c, Counter("d"), Gauge(), [1]), n)

main()
"""

NEGATIVES = """\
from typing import Self
from tpy import int32, Own, Ptr, ReturnException, error_return, dispatch, auto_readonly

class Fail(Exception, ReturnException):
    pass

class Plain:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def __eq__(self, other: Plain) -> bool:
        return self.n == other.n
    def __hash__(self) -> int32:
        return self.n
    def __bool__(self) -> bool:
        return self.n != 0
    def __getitem__(self, i: int32) -> int32:
        return self.n + i
    def __setitem__(self, i: int32, v: int32) -> None:
        self.n = i + v
    def __copy__(self) -> Own[Plain]:
        return Plain(self.n)
    def __del__(self) -> None:
        pass
    def __move__(self, other: Own[Plain]) -> None:
        self.n = other.n
    @property
    def value(self) -> int32:
        return self.n
    @value.setter
    def value(self, v: int32) -> None:
        self.n = v
    def generic[U](self, u: U) -> U:
        return u
    def take(self: Own[Self]) -> Own[Self]:
        return self
    @staticmethod
    def make(n: int32) -> int32:
        return n + 1
    @error_return(Fail)
    def checked(self) -> int32:
        if self.n < 0:
            raise Fail()
        return self.n
    async def fetch(self) -> int32:
        return self.n
    @dispatch
    def put(self, x: int32) -> int32:
        return x
    @dispatch
    def put(self, x: str) -> int32:
        return 2
    def bump(self) -> None:
        self.n += 1

class Box[T]:
    item: T
    def __init__(self, item: T) -> None:
        self.item = item
    def get(self) -> T:
        return self.item

class Parent:
    n: int32
    def __init__(self) -> None:
        self.n = 0

class Child(Parent):
    def __init__(self) -> None:
        super().__init__()
    def extra(self) -> int32:
        return self.n

class Built:
    n: int32
    @dispatch
    def __init__(self, n: int32) -> None:
        self.n = n
    @dispatch
    def __init__(self) -> None:
        self.n = 0
    @auto_readonly
    def get(self) -> int32:
        return self.n

def built(b: Built) -> int32:
    return b.get()

def calls(p: Plain, q: Plain, b: Box[int32], c: Child) -> int32:
    t = 0
    if p.__eq__(q):
        t += 1
    if p.__bool__():
        t += p.__hash__()
    t += p.value
    p.value = 3
    t += p.generic(4)
    t += Plain.make(1)
    t += p.put(1)
    t += b.get()
    t += c.extra()
    return t

@error_return(Fail)
def via_error_return(p: Plain) -> int32:
    return p.checked()

def via_ptr(ptr: Ptr[Plain]) -> None:
    ptr.bump()

def main() -> None:
    p = Plain(1)
    print(calls(p, Plain(2), Box(3), Child()), p.take().n, built(Built(4)), Built().n)

main()
"""

OVERLOADS = """\
from typing import overload
from tpy import int32

class Picker:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    @overload
    def pick(self, x: int32) -> int32: ...
    @overload
    def pick(self, x: int32, y: int32) -> int32: ...
    def pick(self, x: int32, y: int32 = 0) -> int32:
        return x + y

# The two-parameter stub declares exactly the implementation's parameters.
def use(p: Picker) -> int32:
    return p.pick(1) + p.pick(1, 2)
"""

# A parentless record inheriting a @dynamic protocol has virtual methods: a
# `Base` parameter may run `Sub.grow`.
VIRTUAL = """\
from typing import Protocol
from tpy import int32, dynamic

@dynamic
class Shape(Protocol):
    def grow(self) -> None: ...

class Base(Shape):
    items: list[int32]
    def __init__(self) -> None:
        self.items = [1]
    def grow(self) -> None:
        pass
    def plain(self) -> int32:
        return len(self.items)

class Sub(Base):
    def __init__(self) -> None:
        super().__init__()
    def grow(self) -> None:
        self.items.append(2)

def use(b: Base) -> int32:
    b.grow()
    return b.plain()

def main() -> None:
    s = Sub()
    print(use(s), len(s.items))

main()
"""


# A borrowed record result at the access its emitted C++ return type gives it.
RESULTS = """\
from tpy import int32, readonly, pure

class C:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def other(self, o: C) -> C:
        return o
    @readonly
    def other_ro(self, o: C) -> C:
        return o
    def other_w(self, o: C) -> C:
        self.n += 1
        return o
    def me(self) -> C:
        return self
    def other_decl(self, o: C) -> readonly[C]:
        return o

def free(a: C, o: C) -> C:
    return o

@readonly
def free_ro(a: C, o: C) -> C:
    return o

@pure
def free_pure(a: C, o: C) -> C:
    return o

def use(c: C, d: C) -> int32:
    return c.other(d).n + c.other_ro(d).n + c.other_w(d).n + c.me().n + c.other_decl(d).n
"""


class Program:
    def __init__(self, source: str) -> None:
        self.compiler, modules = _compile(source)
        self.entry = _entry(modules)
        ctx = self.compiler.collect_thir(self.entry, tolerate_reject=True)
        self.functions: dict[tuple[str | None, str], list[th.THIRFunction]] = {}
        for func, self_type in iter_module_callables(self.entry.ast, self.entry.analyzer):
            fn = ctx.thir_functions.get(func)
            if fn is not None:
                owner = self_type.name if isinstance(self_type, NominalType) else None
                self.functions.setdefault((owner, func.name), []).append(fn)

    def fn(self, owner: str | None, name: str) -> th.THIRFunction:
        fn, = self.functions[(owner, name)]
        return fn

    def calls(self, owner: str | None, name: str) -> dict[str, list[th.THIRMethodCall]]:
        found: dict[str, list[th.THIRMethodCall]] = {}
        for call in nodes(self.fn(owner, name), th.THIRMethodCall):
            found.setdefault(call.method_cpp, []).append(call)
        return found

    def builder(self, record: str, name: str) -> th.THIRResolvedCallee | None:
        """What a call of `record.name` on a `record` receiver would carry."""
        registry = self.entry.analyzer.registry
        info = registry.get_record(record)
        receiver = NominalType(info.name, _module_qname=info.qualified_name())
        with activate_compiler(self.compiler):
            return next((callee for fi in info.get_method_overloads(name)
                         if (callee := method_callee(fi, receiver, self.entry.analyzer,
                                                     arity=len(fi.params))) is not None), None)


@pytest.fixture(scope="module")
def program() -> Program:
    return Program(SOURCE)


@pytest.fixture(scope="module")
def negatives() -> Program:
    return Program(NEGATIVES)


@pytest.fixture(scope="module")
def results() -> Program:
    return Program(RESULTS)


def _counter() -> NominalType:
    return NominalType("Counter", _module_qname="__main__.Counter")


def test_a_method_definition_binds_its_receiver_first(program: Program) -> None:
    counter = _counter()
    bump = program.fn("Counter", "bump").resolved_callee
    assert bump.identity == th.THIRFunctionIdentity("main", "bump", "__main__.Counter")
    assert bump.signature.param_types == (counter,) and bump.signature.return_type == VoidType()
    assert bump.signature.passings == (ParamPassing.MUT_REF,)
    # An inferred readonly method takes its receiver const.
    total = program.fn("Counter", "total").resolved_callee
    assert total.signature.passings == (ParamPassing.CONST_REF,) and total.signature.return_type == INT32
    push = program.fn("Counter", "push").resolved_callee
    assert push.signature.param_types == (counter, INT32)
    assert push.signature.passings == (ParamPassing.MUT_REF, ParamPassing.VALUE)
    # A record parameter is borrowed at its own verdict; a str parameter is a view.
    merge = program.fn("Counter", "merge").resolved_callee
    # The receiver is the bare record; a record parameter keeps its declared reference.
    assert merge.signature.param_types == (counter, RefType(counter), STR)
    assert merge.signature.passings == (ParamPassing.MUT_REF, ParamPassing.CONST_REF, ParamPassing.VIEW)
    # A free function has no owner and no receiver.
    free = program.fn(None, "free").resolved_callee
    assert free.identity == th.THIRFunctionIdentity("main", "free")
    assert free.signature.param_types == (INT32,)


@pytest.mark.parametrize("owner,name,readonly", [
    ("C", "other", False),       # C& other(C& o) const: an inferred verdict proves the receiver only
    ("C", "other_ro", True),     # const C& other_ro(const C& o) const
    ("C", "other_w", False),     # C& other_w(C& o)
    ("C", "me", False),          # C& me()
    ("C", "other_decl", True),   # const C& other_decl(const C& o) const
    (None, "free", False),       # C& free(const C& a, C& o)
    (None, "free_ro", True),     # const C& free_ro(const C& a, const C& o)
    (None, "free_pure", False),  # C& free_pure(const C& a, C& o)
])
def test_a_borrowed_result_has_the_access_of_its_emitted_return(results: Program, owner: str | None, name: str,
                                                               readonly: bool) -> None:
    callee = results.fn(owner, name).resolved_callee
    result = callee.signature.borrowed_result
    assert result is not None and result.readonly is readonly
    if owner is not None:
        calls = results.calls(None, "use")[name]
        assert calls and all(c.resolved_callee == callee for c in calls)


def test_every_call_carries_its_definitions_callee(program: Program) -> None:
    calls = program.calls(None, "use")
    for name in ("bump", "push", "merge", "total"):
        call = next(c for c in calls[name] if c.resolved_callee is not None
                    and c.resolved_callee.identity.owner == "__main__.Counter")
        assert call.resolved_callee == program.fn("Counter", name).resolved_callee
        assert call.stub_callee is None
    # `self.bump()` inside another method.
    inner = program.calls("Counter", "bump_twice")["bump"]
    assert len(inner) == 2
    assert all(c.resolved_callee == program.fn("Counter", "bump").resolved_callee for c in inner)
    # A native container's method keeps its stub and gets no user callee.
    append, = calls["push_back"]
    assert append.stub_callee is not None and append.resolved_callee is None


def test_same_named_methods_of_two_records_are_distinct_callees(program: Program) -> None:
    counter = program.fn("Counter", "bump").resolved_callee
    gauge = program.fn("Gauge", "bump").resolved_callee
    assert gauge.identity == th.THIRFunctionIdentity("main", "bump", "__main__.Gauge")
    assert counter.identity != gauge.identity and counter.signature != gauge.signature
    called = {c.resolved_callee.identity.owner: c.resolved_callee for c in program.calls(None, "use")["bump"]}
    assert called == {"__main__.Counter": counter, "__main__.Gauge": gauge}


@pytest.mark.parametrize("name", ["__eq__", "__hash__", "__bool__", "__getitem__", "__setitem__"])
def test_a_dunder_body_borrows_its_receiver(negatives: Program, name: str) -> None:
    fn = negatives.fn("Plain", name)
    assert fn.receiver == th.THIRBorrowedRecord(fn.receiver.type, fn.receiver.readonly)
    assert fn.receiver.type.qualified_name() == "__main__.Plain"


def test_a_dunder_with_an_injected_operator_template_is_no_call_target(negatives: Program) -> None:
    # `p.__eq__(q)` renders `(p) == (q)`: the template, not the body, is what runs.
    fn = negatives.fn("Plain", "__eq__")
    assert fn.resolved_callee is None and negatives.builder("Plain", "__eq__") is None
    assert negatives.calls(None, "calls")["__eq__"][0].resolved_callee is None


@pytest.mark.parametrize("name", ["__hash__", "__bool__"])
def test_a_template_less_dunder_is_an_ordinary_call_target(negatives: Program, name: str) -> None:
    fn = negatives.fn("Plain", name)
    assert fn.resolved_callee is not None and negatives.builder("Plain", name) == fn.resolved_callee
    assert negatives.calls(None, "calls")[name][0].resolved_callee == fn.resolved_callee


@pytest.mark.parametrize("name", ["__del__", "__copy__", "__move__"])
def test_a_lifecycle_hook_has_no_receiver_fact(negatives: Program, name: str) -> None:
    fn = negatives.fn("Plain", name)
    assert fn.receiver is None and fn.resolved_callee is None
    assert negatives.builder("Plain", name) is None


def test_a_constructor_body_has_no_receiver_fact(negatives: Program) -> None:
    # The second `@dispatch` body is fed as a method; its `self` is under construction.
    init, = negatives.functions[("Built", "__init__")]
    assert init.receiver is None and init.resolved_callee is None
    assert negatives.builder("Built", "__init__") is None


def test_an_auto_readonly_def_has_no_receiver_fact_and_no_callee(negatives: Program) -> None:
    clones = negatives.functions[("Built", "get")]
    assert len(clones) == 2 and all(fn.receiver is None and fn.resolved_callee is None for fn in clones)
    call, = nodes(negatives.fn(None, "built"), th.THIRMethodCall)
    assert call.method_cpp == "get" and call.resolved_callee is None
    assert negatives.builder("Built", "get") is None


def test_property_accessors_have_no_receiver_fact_and_no_callee(negatives: Program) -> None:
    getter, setter = negatives.functions[("Plain", "value")]
    assert getter.receiver is None and setter.receiver is None
    assert getter.resolved_callee is None and setter.resolved_callee is None
    calls = negatives.calls(None, "calls")
    assert [c.resolved_callee for c in calls["value"] + calls["set_value"]] == [None, None]
    assert negatives.builder("Plain", "value") is None


@pytest.mark.parametrize("record,name,member", [
    ("Plain", "__eq__", None),          # explicit dunder call: an operator template
    ("Plain", "generic", "generic"),    # generic method
    ("Plain", "put", "put"),            # a @dispatch group has two bodies
    ("Box", "get", "get"),              # generic record
    ("Child", "extra", "extra"),        # record with a parent
])
def test_an_unproven_call_target_has_no_callee_at_either_side(negatives: Program, record: str, name: str,
                                                              member: str | None) -> None:
    for fn in negatives.functions.get((record, name), []):
        assert fn.resolved_callee is None
    assert negatives.builder(record, name) is None
    calls = [c for c in nodes(negatives.fn(None, "calls"), th.THIRMethodCall)
             if member is None and c.cpp_template is not None or c.method_cpp == member]
    assert calls and all(c.resolved_callee is None for c in calls)


@pytest.mark.parametrize("name", ["take", "make", "checked", "fetch"])
def test_consuming_static_error_return_and_async_methods_have_no_callee(negatives: Program, name: str) -> None:
    for fn in negatives.functions.get(("Plain", name), []):
        assert fn.resolved_callee is None
    assert negatives.builder("Plain", name) is None
    # `Plain.make(1)` is a free-call node that never resolves to a method.
    assert all(c.resolved_callee is None for c in nodes(negatives.fn(None, "calls"), th.THIRCall))
    assert all(c.resolved_callee is None for c in nodes(negatives.fn(None, "via_error_return"), th.THIRMethodCall))


def test_a_pointer_receiver_has_no_callee(negatives: Program) -> None:
    call, = nodes(negatives.fn(None, "via_ptr"), th.THIRMethodCall)
    assert call.method_cpp == "bump" and call.resolved_callee is None
    assert negatives.fn("Plain", "bump").resolved_callee is not None


def test_an_overload_group_has_no_callee() -> None:
    overloads = Program(OVERLOADS)
    assert all(fn.resolved_callee is None for fn in overloads.functions.get(("Picker", "pick"), []))
    assert overloads.builder("Picker", "pick") is None
    assert all(c.resolved_callee is None for c in nodes(overloads.fn(None, "use"), th.THIRMethodCall))


def test_a_virtual_owner_has_a_receiver_but_no_callee() -> None:
    virtual = Program(VIRTUAL)
    for name in ("grow", "plain"):
        fn = virtual.fn("Base", name)
        assert fn.receiver is not None and fn.resolved_callee is None
        assert virtual.builder("Base", name) is None
    calls = nodes(virtual.fn(None, "use"), th.THIRMethodCall)
    assert {c.method_cpp for c in calls} == {"grow", "plain"}
    assert all(c.resolved_callee is None for c in calls)


# --- validator ---------------------------------------------------------------


def test_validator_rejects_a_definition_callee_that_disagrees_with_its_receiver(program: Program) -> None:
    fn = program.fn("Counter", "push")
    validate_function(fn)
    callee = fn.resolved_callee
    signature = callee.signature
    for damaged in (
            replace(fn, receiver=None),  # owner without receiver
            replace(fn, resolved_callee=replace(callee, identity=replace(callee.identity, owner=None))),
            replace(fn, resolved_callee=replace(callee, identity=replace(callee.identity, owner="__main__.Gauge"))),
            replace(fn, resolved_callee=replace(callee, signature=replace(
                signature, param_types=(INT32, *signature.param_types[1:])))),
            replace(fn, resolved_callee=replace(callee, signature=replace(
                signature, passings=(ParamPassing.CONST_REF, *signature.passings[1:])))),
            # The declared parameters follow the receiver.
            replace(fn, resolved_callee=replace(callee, signature=replace(
                signature, param_types=signature.param_types[:1], passings=signature.passings[:1])))):
        with pytest.raises(THIRValidationError, match="resolved callee disagrees with definition"):
            validate_function(damaged)
    with pytest.raises(THIRValidationError, match="invalid resolved callee"):
        validate_function(replace(fn, resolved_callee=replace(callee, identity=replace(callee.identity, owner=""))))


def test_validator_rejects_an_owner_on_a_free_call(program: Program) -> None:
    fn = program.fn(None, "main")
    call = next(c for c in nodes(fn, th.THIRCall) if c.resolved_callee is not None)
    damaged = replace(call.resolved_callee, identity=replace(call.resolved_callee.identity, owner="__main__.Counter"))
    with pytest.raises(THIRValidationError, match="resolved callee on incompatible call"):
        validate_function(_replace_node(fn, call, replace(call, resolved_callee=damaged)))


def test_validator_rejects_a_method_callee_that_disagrees_with_its_call(program: Program) -> None:
    fn = program.fn(None, "use")
    validate_function(fn)
    call = next(c for c in program.calls(None, "use")["push"] if c.resolved_callee is not None)
    callee = call.resolved_callee
    signature = callee.signature
    append, = program.calls(None, "use")["push_back"]
    with pytest.raises(THIRValidationError, match="call carries both a resolved and a stub callee"):
        validate_function(_replace_node(fn, call, replace(call, stub_callee=append.stub_callee)))
    for damaged in (
            replace(call, resolved_callee=replace(callee, identity=replace(callee.identity, owner=None))),
            replace(call, resolved_callee=replace(callee, identity=replace(callee.identity, owner="__main__.Gauge"))),
            replace(call, args=()),  # arity
            replace(call, resolved_callee=replace(callee, signature=replace(
                signature, param_types=(INT32, *signature.param_types[1:])))),
            replace(call, resolved_callee=replace(callee, signature=replace(
                signature, passings=(ParamPassing.VALUE, *signature.passings[1:])))),
            replace(call, cpp_template="({self}).push({0})"),
            replace(call, native_function_name="::tpy::push"),
            replace(call, method_targs_cpp=("int32_t",)),
            replace(call, deref_chain=1),
            replace(call, move_receiver=True),
            replace(call, callable_value_unwrap=True)):
        with pytest.raises(THIRValidationError, match="resolved method callee on incompatible call"):
            validate_function(_replace_node(fn, call, damaged))


def test_dump_lists_method_callees_and_owner_signatures(program: Program) -> None:
    ctx = program.compiler.collect_thir(program.entry, tolerate_reject=True)
    out = dump_codegen_thir(program.entry.ast, program.entry.analyzer, ctx)
    assert "  signature __main__.Counter(Counter: mut_ref, int32: value) -> reference" in out
    assert "    [0] bump: callee __main__.Counter.bump(Counter: mut_ref) -> reference" in out
    assert "callee __main__.Gauge.bump(Gauge: mut_ref) -> reference" in out
    # A native method stub's call is not listed.
    assert "push_back" not in "".join(line for line in out.splitlines() if "callee" in line)
