"""The callee fact THIR publishes for a user record's instance method,
property accessor and `@auto_readonly` def: one identity (module, name,
owning record, accessor role) and one signature whose parameter 0 is the
receiver, the same value at the definition and at every call that
statically resolves to it (a follows-receiver signature publishes the
definition's result; MIR derives each call's access from the receiver it
binds). Analysis only -- no render reads it."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..type_def_registry import ParamPassing
from ..typesys import INT32, STR, NominalType, OwnType, ReadonlyType, RefType, VoidType, unwrap_ref_type
from . import nodes as th
from .dump import dump_codegen_thir
from .lower import iter_module_callables
from ..mir.call_contract import bound_result
from .lower.callables import method_callee
from .test_method_stubs import _replace_node, nodes
from .testutil import _compile, _entry
from . import validate as validate_module
from .validate import THIRValidationError, validate_definitions, validate_function

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


def test_an_auto_readonly_def_publishes_one_callee_from_both_clones(negatives: Program) -> None:
    mutable, const = negatives.functions[("Built", "get")]
    assert mutable.receiver is not None and not mutable.receiver.readonly and mutable.access_twin
    assert const.receiver is not None and const.receiver.readonly and not const.access_twin
    callee = const.resolved_callee
    assert callee is not None and mutable.resolved_callee == callee
    assert callee.identity == th.THIRFunctionIdentity("main", "get", "__main__.Built")
    assert callee.signature.passings == (ParamPassing.CONST_REF,) and callee.signature.result_follows_receiver
    call, = nodes(negatives.fn(None, "built"), th.THIRMethodCall)
    assert call.method_cpp == "get" and call.resolved_callee == callee
    assert negatives.builder("Built", "get") == callee


def test_property_accessors_publish_a_receiver_and_a_role_identity(negatives: Program) -> None:
    getter, setter = negatives.functions[("Plain", "value")]
    assert getter.receiver.readonly and not setter.receiver.readonly
    assert getter.resolved_callee.identity == th.THIRFunctionIdentity("main", "value", "__main__.Plain", "fget")
    assert setter.resolved_callee.identity == th.THIRFunctionIdentity("main", "value", "__main__.Plain", "fset")
    calls = negatives.calls(None, "calls")
    assert [c.resolved_callee for c in calls["value"] + calls["set_value"]] == [getter.resolved_callee,
                                                                               setter.resolved_callee]


# Property accessors and `@auto_readonly` twins: one callable per role.
ACCESSORS = """\
from tpy import int32, readonly, auto_readonly, Own

class R:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

class Counter:
    n: int32
    s: str
    o: str
    r: R
    xs: list[int32]
    def __init__(self) -> None:
        self.n = 0
        self.s = ""
        self.o = ""
        self.r = R(0)
        self.xs = []
    @property
    def count(self) -> int32:
        return self.n
    @count.setter
    def count(self, v: int32) -> None:
        self.n = v
    @property
    def elems(self) -> list[int32]:
        return self.xs
    @elems.setter
    def elems(self, v: list[int32]) -> None:
        self.xs = v
    @property
    def label(self) -> str:
        return self.s
    @label.setter
    def label(self, v: str) -> None:
        self.s = v
    @property
    def owned(self) -> str:
        return self.o
    @owned.setter
    def owned(self, v: Own[str]) -> None:
        self.o = v
    @property
    def rec(self) -> R:
        return self.r
    @rec.setter
    def rec(self, v: R) -> None:
        self.r = v
    @property
    def frozen_rec(self) -> R:
        return self.r
    @frozen_rec.setter
    def frozen_rec(self, v: readonly[R]) -> None:
        self.n = 9
    @property
    def own_rec(self) -> R:
        return self.r
    @own_rec.setter
    def own_rec(self, v: Own[R]) -> None:
        self.r = v
    @auto_readonly
    def me(self) -> Counter:
        return self
    @auto_readonly
    def frozen(self) -> readonly[Counter]:
        return self
    @auto_readonly
    def via(self) -> Counter:
        return self.me()
    # The clones take `other` at two accesses: two signatures, no one callable.
    @auto_readonly
    def pick(self, other: Counter) -> Counter:
        return other

def use(c: Counter, r: readonly[Counter], d: Counter) -> int32:
    c.count = c.count + 1
    c.elems = [1]
    c.label = "a"
    c.owned = "b"
    c.rec = R(2)
    c.frozen_rec = R(3)
    c.own_rec = R(5)
    m = c.me()
    m.count = 4
    t = r.me().count + c.frozen().count + r.frozen().count + c.via().count
    return t + c.pick(d).count + len(c.elems) + c.rec.x

def main() -> None:
    c = Counter()
    print(use(c, Counter(), Counter()), c.label, c.owned, c.frozen_rec.x)

main()
"""

# A method and a property sharing a name: the member call cannot tell them apart.
CLASH = """\
from tpy import int32

class Clash:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def val(self) -> int32:
        return 1
    @property
    def val(self) -> int32:
        return self.n

def use(k: Clash) -> int32:
    return k.val
"""

# Accessors and twins of a record no call can pin to one body.
KEPT = """\
from typing import Iterator, Protocol, Self
from tpy import int32, auto_own, auto_readonly, dynamic

class Box[T]:
    item: T
    def __init__(self, item: T) -> None:
        self.item = item
    @property
    def value(self) -> T:
        return self.item
    @auto_readonly
    def get(self) -> T:
        return self.item

@dynamic
class Shape(Protocol):
    def grow(self) -> None: ...

class Base(Shape):
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def grow(self) -> None:
        pass
    @property
    def size(self) -> int32:
        return self.n

class Parent:
    n: int32
    def __init__(self) -> None:
        self.n = 0

class Child(Parent):
    def __init__(self) -> None:
        super().__init__()
    @property
    def size(self) -> int32:
        return self.n

class Kinds:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def gen(self) -> Iterator[int32]:
        yield self.n
    @classmethod
    def build(cls) -> int32:
        return 1
    def take(self: auto_own[Self]) -> auto_own[Self]:
        return self

def use(b: Box[int32], s: Base, c: Child) -> int32:
    return b.value + b.get() + s.size + c.size

def kinds(q: Kinds) -> int32:
    t = Kinds.build()
    for x in q.gen():
        t += x
    return t + q.take().n
"""


@pytest.fixture(scope="module")
def accessors() -> Program:
    return Program(ACCESSORS)


def _readonly(typ) -> bool:
    return isinstance(typ, ReadonlyType) or isinstance(unwrap_ref_type(typ), ReadonlyType)


def _roles(program: Program, name: str) -> dict[str, list[th.THIRFunction]]:
    """The bodies of `Counter.name` by role: getter clones, setter, method clones."""
    roles: dict[str, list[th.THIRFunction]] = {}
    for fn in program.functions[("Counter", name)]:
        identity = fn.resolved_callee.identity if fn.resolved_callee is not None else None
        roles.setdefault(identity.accessor or "method" if identity is not None else "none", []).append(fn)
    return roles


def test_a_value_getter_has_one_body_and_a_setter_its_own_identity(accessors: Program) -> None:
    roles = _roles(accessors, "count")
    getter, = roles["fget"]  # the mutable clone of a value-returning getter is pruned
    setter, = roles["fset"]
    counter = NominalType("Counter", _module_qname="__main__.Counter")
    assert getter.receiver == th.THIRBorrowedRecord(counter, True) and not getter.access_twin
    assert setter.receiver == th.THIRBorrowedRecord(counter, False)
    fget, fset = getter.resolved_callee, setter.resolved_callee
    assert fget.identity == th.THIRFunctionIdentity("main", "count", "__main__.Counter", "fget")
    assert fget.signature.param_types == (counter,) and fget.signature.return_type == INT32
    assert fget.signature.passings == (ParamPassing.CONST_REF,) and fget.signature.result_follows_receiver
    assert fset.identity == th.THIRFunctionIdentity("main", "count", "__main__.Counter", "fset")
    assert fset.signature.passings == (ParamPassing.MUT_REF, ParamPassing.VALUE)
    assert not fset.signature.result_follows_receiver and fset.signature.return_type == VoidType()
    calls = accessors.calls(None, "use")
    assert all(c.resolved_callee == fget for c in calls["count"])
    assert all(c.resolved_callee == fset for c in calls["set_count"])


def test_a_reference_getter_publishes_one_callee_from_both_clones(accessors: Program) -> None:
    mutable, const = _roles(accessors, "elems")["fget"]
    assert mutable.access_twin and not mutable.receiver.readonly
    assert not const.access_twin and const.receiver.readonly
    assert mutable.resolved_callee == const.resolved_callee
    signature = const.resolved_callee.signature
    # A container result is no borrowed record.
    assert signature.borrowed_result is None and signature.result_follows_receiver
    assert signature.passings == (ParamPassing.CONST_REF,)
    call, = accessors.calls(None, "use")["elems"]
    assert call.resolved_callee == const.resolved_callee


@pytest.mark.parametrize("name,passing", [
    ("count", ParamPassing.VALUE),       # int32: void set_count(int32_t v)
    ("label", ParamPassing.VIEW),        # str: void set_label(std::string_view v)
    ("owned", ParamPassing.VALUE),       # Own[str]: void set_owned(std::string v)
    ("rec", ParamPassing.OWN),           # R, expanded to Own[R]: void set_rec(R&& v)
    ("frozen_rec", ParamPassing.OWN),    # readonly[R], expanded to Own[readonly[R]]: R&& v
    ("own_rec", ParamPassing.OWN),       # Own[R] spelled out: void set_own_rec(R&& v)
    ("elems", ParamPassing.OWN),         # list[int32], expanded to Own[list[int32]]
])
def test_a_setter_passes_its_value_as_its_body_declares(accessors: Program, name: str,
                                                        passing: ParamPassing) -> None:
    setter, = _roles(accessors, name)["fset"]
    callee = setter.resolved_callee
    assert callee.signature.passings == (ParamPassing.MUT_REF, passing)
    assert tuple(p.passing for p in th.effective_params(setter)) == callee.signature.passings
    calls = accessors.calls(None, "use")[f"set_{name}"]
    assert calls and all(call.resolved_callee == callee for call in calls)


@pytest.mark.parametrize("name,caller,receiver_readonly,readonly", [
    ("me", "use", False, False),       # Counter& me() on a mutable receiver
    ("me", "use", True, True),         # const Counter& me() const on a readonly one
    ("frozen", "use", False, True),    # an explicit readonly[...] return stays readonly
    ("frozen", "use", True, True),
    ("me", "via", False, False),       # inside the mutable clone: this->me() is mutable
    ("me", "via", True, True),
])
def test_a_twin_result_has_the_access_of_the_receiver_at_the_call(
        accessors: Program, name: str, caller: str, receiver_readonly: bool, readonly: bool) -> None:
    definition = next(fn for fn in accessors.functions[("Counter", name)] if not fn.access_twin)
    owner = None if caller == "use" else "Counter"
    calls = [c for fn in accessors.functions[(owner, caller)] for c in nodes(fn, th.THIRMethodCall)
             if c.method_cpp == name and _readonly(c.receiver.result_type) is receiver_readonly]
    assert calls
    for call in calls:
        # Every call publishes the definition's signature; its access is derived from its receiver.
        assert call.resolved_callee == definition.resolved_callee
        assert bound_result(call.resolved_callee.signature, receiver_readonly).readonly is readonly
    # The definition is the const clone: its result is readonly.
    assert definition.resolved_callee.signature.borrowed_result.readonly


def test_a_twin_whose_clones_take_parameters_at_two_accesses_is_no_callable(accessors: Program) -> None:
    clones = accessors.functions[("Counter", "pick")]
    assert len(clones) == 2 and all(fn.receiver is not None and fn.resolved_callee is None for fn in clones)
    call, = accessors.calls(None, "use")["pick"]
    assert call.resolved_callee is None and accessors.builder("Counter", "pick") is None


def test_a_method_and_a_property_sharing_a_name_publish_no_callee() -> None:
    clash = Program(CLASH)
    assert all(fn.resolved_callee is None for fn in clash.functions[("Clash", "val")])
    call, = clash.calls(None, "use")["val"]
    assert call.resolved_callee is None
    with activate_compiler(clash.compiler):
        assert clash.compiler.callable_body("__main__.Clash", "val", None) is None
        assert clash.compiler.callable_body("__main__.Clash", "val", "fget") is None


@pytest.fixture(scope="module")
def kept() -> Program:
    return Program(KEPT)


def _calls_on(program: Program, record: str, name: str) -> list[th.THIRMethodCall]:
    return [c for c in nodes(program.fn(None, "use"), th.THIRMethodCall)
            if c.method_cpp == name and unwrap_ref_type(c.receiver.result_type).name == record]


@pytest.mark.parametrize("record,name", [("Box", "value"), ("Box", "get"), ("Base", "size")])
def test_generic_and_virtual_owners_keep_accessors_unresolved(kept: Program, record: str, name: str) -> None:
    assert all(fn.resolved_callee is None for fn in kept.functions.get((record, name), []))
    calls = _calls_on(kept, record, name)
    assert calls and all(c.resolved_callee is None for c in calls)


def test_a_derived_owner_resolves_its_own_accessor(kept: Program) -> None:
    # A record with a plain parent is modeled: its own getter is a callee.
    call, = _calls_on(kept, "Child", "size")
    callee = call.resolved_callee
    assert callee is not None and callee.identity.owner == "__main__.Child" and callee.identity.accessor == "fget"
    assert callee.signature.param_types[0].name == "Child" and call.receiver_access.type.name == "Child"
    assert kept.fn("Child", "size").resolved_callee == callee


def test_class_generator_and_auto_own_methods_keep_no_callee(kept: Program) -> None:
    for name in ("build", "gen", "take"):
        assert all(fn.resolved_callee is None for fn in kept.functions.get(("Kinds", name), []))
    calls = nodes(kept.fn(None, "kinds"), th.THIRMethodCall)
    assert {c.method_cpp for c in calls} >= {"gen", "take"}
    assert all(c.resolved_callee is None for c in calls)
    assert all(c.resolved_callee is None for c in nodes(kept.fn(None, "kinds"), th.THIRCall))


def test_compiler_finds_one_body_per_role(accessors: Program) -> None:
    compiler = accessors.compiler
    owner = "__main__.Counter"
    getter = compiler.callable_body(owner, "elems", "fget")
    assert getter.is_property_getter and getter.auto_readonly_polarity == "apply"
    setter = compiler.callable_body(owner, "elems", "fset")
    assert setter.is_property_setter
    assert compiler.callable_body(owner, "elems", None) is None
    assert compiler.callable_body(owner, "me", None).auto_readonly_polarity == "apply"
    assert compiler.callable_body(owner, "pick", None) is None
    # The loop-frame rule keeps its own uniqueness question.
    assert compiler.single_method_body(owner, "me") is None
    assert compiler.single_method_body(owner, "elems") is None


_LIST_I32 = NominalType("list", (INT32,), _module_qname="builtins.list")


# The one callee each role publishes: (identity, parameter types, return
# type, borrowed result readonly or None, passings, follows receiver) --
# literal values, so a change in any role's facts fails here.
@pytest.mark.parametrize("fixture,role,name,expected", [
    ("program", None, "merge", (
        th.THIRFunctionIdentity("main", "merge", "__main__.Counter"),
        (_counter(), RefType(_counter()), STR), VoidType(), None,
        (ParamPassing.MUT_REF, ParamPassing.CONST_REF, ParamPassing.VIEW), False)),
    ("accessors", None, "me", (
        th.THIRFunctionIdentity("main", "me", "__main__.Counter"),
        (_counter(),), RefType(_counter()), True, (ParamPassing.CONST_REF,), True)),
    ("accessors", "fget", "count", (
        th.THIRFunctionIdentity("main", "count", "__main__.Counter", "fget"),
        (_counter(),), INT32, None, (ParamPassing.CONST_REF,), True)),
    ("accessors", "fget", "elems", (
        th.THIRFunctionIdentity("main", "elems", "__main__.Counter", "fget"),
        (_counter(),), RefType(_LIST_I32), None, (ParamPassing.CONST_REF,), True)),
    ("accessors", "fset", "elems", (
        th.THIRFunctionIdentity("main", "elems", "__main__.Counter", "fset"),
        (_counter(), OwnType(_LIST_I32)), VoidType(), None, (ParamPassing.MUT_REF, ParamPassing.OWN), False)),
    ("accessors", "fset", "label", (
        th.THIRFunctionIdentity("main", "label", "__main__.Counter", "fset"),
        (_counter(), STR), VoidType(), None, (ParamPassing.MUT_REF, ParamPassing.VIEW), False)),
])
def test_one_callee_path_serves_every_role(request: pytest.FixtureRequest, fixture: str, role: str | None,
                                           name: str, expected: tuple) -> None:
    program: Program = request.getfixturevalue(fixture)
    info = program.entry.analyzer.registry.get_record("Counter")
    prop = info.properties.get(name)
    fis = (info.get_method_overloads(name) if role is None
           else [prop.getter if role == "fget" else prop.setter])
    receiver = _counter()
    with activate_compiler(program.compiler):
        # Every FunctionInfo sema may bind for the role (both clones of a
        # twin) publishes the one callee.
        callees = {method_callee(fi, receiver, program.entry.analyzer, arity=len(fi.params)) for fi in fis}
    callee, = callees
    signature = callee.signature
    result = signature.borrowed_result
    assert (callee.identity, signature.param_types, signature.return_type,
            None if result is None else result.readonly, signature.passings,
            signature.result_follows_receiver) == expected
    definitions = [fn for fn in program.functions[("Counter", name)]
                   if fn.resolved_callee is not None and fn.resolved_callee.identity.accessor == role]
    assert definitions and all(fn.resolved_callee == callee for fn in definitions)


def test_sema_links_each_mutable_clone_to_its_const_clone(accessors: Program) -> None:
    record = next(r for r in accessors.entry.ast.records if r.name == "Counter")
    links = {(m.name, m.auto_readonly_polarity, m.is_property_setter): m.clone_of for m in record.methods}
    bodies = accessors.compiler.method_bodies
    for name in ("elems", "me", "frozen", "via", "pick"):
        mutable, const = (b for b in bodies[("__main__.Counter", name)] if not b.is_property_setter)
        assert mutable.clone_of is const and const.clone_of is None
    # A value getter's pruned mutable clone keeps its link in the body table.
    count_mutable, count_const = (b for b in bodies[("__main__.Counter", "count")] if b.is_property_getter)
    assert count_mutable.clone_of is count_const
    assert links[("elems", None, True)] is None  # a setter is no clone
    assert all(m.clone_of is None for m in record.methods if m.auto_readonly_polarity != "strip")
    # The clones that take `other` at two accesses stay linked; the body
    # lookup refuses them on their differing signatures.
    assert accessors.compiler.callable_body("__main__.Counter", "pick", None) is None


@pytest.mark.parametrize("record,name,member", [
    ("Plain", "__eq__", None),          # explicit dunder call: an operator template
    ("Plain", "generic", "generic"),    # generic method
    ("Plain", "put", "put"),            # a @dispatch group has two bodies
    ("Box", "get", "get"),              # generic record
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


def _clones(program: Program, name: str) -> tuple[th.THIRFunction, th.THIRFunction]:
    mutable, const = program.functions[("Counter", name)]
    assert mutable.access_twin and not const.access_twin
    return mutable, const


def test_validator_rejects_a_follows_receiver_signature_on_a_mutable_receiver(accessors: Program) -> None:
    _, const = _clones(accessors, "me")
    callee = const.resolved_callee
    damaged = replace(callee, signature=replace(callee.signature, passings=(ParamPassing.MUT_REF,)))
    with pytest.raises(THIRValidationError, match="follows-receiver signature needs a const receiver"):
        validate_function(replace(const, resolved_callee=damaged))


def test_validator_rejects_a_mutable_follows_receiver_result(accessors: Program) -> None:
    fn = accessors.fn(None, "use")
    validate_function(fn)
    # Even at a mutable receiver the call publishes the const clone's readonly result.
    call = next(c for c in accessors.calls(None, "use")["me"] if not _readonly(c.receiver.result_type))
    callee = call.resolved_callee
    result = callee.signature.borrowed_result
    assert result.readonly
    mutable = replace(callee, signature=replace(callee.signature, borrowed_result=replace(result, readonly=False)))
    with pytest.raises(THIRValidationError, match="follows-receiver signature needs a readonly result"):
        validate_function(_replace_node(fn, call, replace(call, resolved_callee=mutable)))
    for clone in _clones(accessors, "me"):
        with pytest.raises(THIRValidationError, match="follows-receiver signature needs a readonly result"):
            validate_function(replace(clone, resolved_callee=mutable))


def test_validator_holds_the_twin_to_its_definition(accessors: Program) -> None:
    mutable, const = _clones(accessors, "me")
    validate_function(mutable)
    validate_function(const)
    validate_definitions((mutable, const))
    # Unmarked, the mutable clone's receiver contradicts the const receiver it publishes.
    with pytest.raises(THIRValidationError, match="resolved callee disagrees with definition"):
        validate_function(replace(mutable, access_twin=False))
    with pytest.raises(THIRValidationError, match="access twin needs a mutable receiver"):
        validate_function(replace(const, access_twin=True))
    with pytest.raises(THIRValidationError, match="access twin needs a mutable receiver"):
        validate_function(replace(mutable, resolved_callee=None))
    with pytest.raises(THIRValidationError, match="two bodies define one callee identity"):
        validate_definitions((replace(mutable, access_twin=False), const))
    with pytest.raises(THIRValidationError, match="two bodies define one callee identity"):
        validate_definitions((mutable, mutable, const))
    other = replace(const.resolved_callee, signature=replace(const.resolved_callee.signature, borrowed_result=None))
    with pytest.raises(THIRValidationError, match="access twin disagrees with its definition"):
        validate_definitions((replace(mutable, resolved_callee=other), const))


def test_the_codegen_pass_checks_a_modules_definitions_together(monkeypatch: pytest.MonkeyPatch) -> None:
    # THIR enforces its own invariant: no MIR analysis runs here.
    checked: list[tuple[th.THIRFunction, ...]] = []
    real = validate_module.validate_definitions

    def spy(functions: tuple[th.THIRFunction, ...]) -> None:
        checked.append(tuple(functions))
        real(functions)

    monkeypatch.setattr(validate_module, "validate_definitions", spy)
    program = Program(ACCESSORS)
    ctx = program.compiler.collect_thir(program.entry, tolerate_reject=True)
    bodies = tuple(ctx.thir_functions.values())
    assert any(len(group) == len(bodies) and all(a is b for a, b in zip(group, bodies)) for group in checked)

    def duplicated(functions: tuple[th.THIRFunction, ...]) -> None:
        # The twin, unmarked, claims the definition's identity a second time.
        twins = [replace(fn, access_twin=False) for fn in functions if fn.access_twin]
        real((*functions, *twins[:1]))

    monkeypatch.setattr(validate_module, "validate_definitions", duplicated)
    with pytest.raises(THIRValidationError, match="two bodies define one callee identity"):
        program.compiler.collect_thir(program.entry, tolerate_reject=True)


def test_validator_rejects_a_definition_whose_passings_differ_from_its_parameters(accessors: Program) -> None:
    setter, = _roles(accessors, "rec")["fset"]
    validate_function(setter)
    callee = setter.resolved_callee
    for passing in (ParamPassing.CONST_REF, ParamPassing.MUT_REF):
        damaged = replace(callee, signature=replace(callee.signature, passings=(ParamPassing.MUT_REF, passing)))
        with pytest.raises(THIRValidationError, match="resolved callee disagrees with definition"):
            validate_function(replace(setter, resolved_callee=damaged))


def test_validator_rejects_a_malformed_accessor_identity(accessors: Program) -> None:
    getter, = _roles(accessors, "count")["fget"]
    callee = getter.resolved_callee
    for accessor in ("fset", "get"):
        damaged = replace(callee, identity=replace(callee.identity, accessor=accessor))
        with pytest.raises(THIRValidationError, match="invalid accessor identity"):
            validate_function(replace(getter, resolved_callee=damaged))


def test_dump_names_the_accessor_and_the_follows_receiver_bit(accessors: Program) -> None:
    ctx = accessors.compiler.collect_thir(accessors.entry, tolerate_reject=True)
    out = dump_codegen_thir(accessors.entry.ast, accessors.entry.analyzer, ctx)
    assert "  signature __main__.Counter.count.fget(Counter: const_ref) -> storage, follows receiver" in out
    assert "  signature __main__.Counter.count.fset(Counter: mut_ref, int32: value) -> reference" in out
    assert "callee __main__.Counter.count.fset(Counter: mut_ref, int32: value) -> reference" in out
    # Both clones of a twin print the one callable.
    assert out.count("  signature __main__.Counter(Counter: const_ref) -> reference, follows receiver") >= 6


# --- the receiver access a call is emitted at --------------------------------

RECEIVERS = """\
from tpy import int32, readonly, auto_readonly

class Bag:
    xs: list[int32]
    def __init__(self) -> None:
        self.xs = []
    @auto_readonly
    def items(self) -> list[int32]:
        return self.xs
    def push(self, v: int32) -> None:
        self.xs.append(v)
    @auto_readonly
    def again(self) -> list[int32]:
        return self.items()
    @auto_readonly
    def total(self) -> int32:
        t = 0
        for x in self.items():
            t += x
        return t

class Holder:
    bag: Bag
    def __init__(self) -> None:
        self.bag = Bag()
    @auto_readonly
    def through(self) -> int32:
        t = 0
        for x in self.bag.items():
            t += x
        return t

def const_alias(b: readonly[Bag]) -> int32:
    c = b
    t = 0
    for x in c.items():
        t += x
    return t

def loop_var(bs: readonly[list[Bag]]) -> int32:
    t = 0
    for b in bs:
        for x in b.items():
            t += x
    return t

def mutable(b: Bag) -> int32:
    b.push(1)
    t = 0
    for x in b.items():
        t += x
    return t

def declared(b: readonly[Bag]) -> int32:
    t = 0
    for x in b.items():
        t += x
    return t

def inferred(b: Bag) -> int32:
    t = 0
    for x in b.items():
        t += x
    return t
"""


@pytest.fixture(scope="module")
def receivers() -> Program:
    return Program(RECEIVERS)


@pytest.mark.parametrize("name,readonly", [
    ("mutable", False),    # Bag& b: the mutable overload
    ("declared", True),    # const Bag& b, from readonly[Bag]
    ("inferred", True),    # const Bag& b, from the inferred verdict: the static type stays mutable
])
def test_a_call_carries_the_access_its_receiver_is_emitted_at(receivers: Program, name: str,
                                                              readonly: bool) -> None:
    fn = receivers.fn(None, name)
    calls = nodes(fn, th.THIRMethodCall)
    assert calls and all(c.receiver_access == th.THIRBorrowedRecord(c.resolved_callee.signature.param_types[0],
                                                                     readonly if c.method_cpp == "items" else False)
                         for c in calls)
    # The loop over the follows-receiver result walks it at that access.
    loop, = nodes(fn, th.THIRForEach)
    assert loop.iteration.source.readonly is readonly


def test_a_self_receiver_has_the_access_of_its_clone(receivers: Program) -> None:
    mutable, const = receivers.functions[("Bag", "again")]
    assert mutable.access_twin and not const.access_twin
    for fn, readonly in ((mutable, False), (const, True)):
        call, = nodes(fn, th.THIRMethodCall)
        assert call.receiver_access.readonly is readonly


@pytest.mark.parametrize("owner,name", [("Bag", "total"), ("Holder", "through")])
def test_a_loop_in_each_clone_walks_at_its_clone_access(receivers: Program, owner: str, name: str) -> None:
    # `self.items()` and the field path `self.bag.items()`: the mutable clone
    # binds the mutable overload, the const clone the const one.
    mutable, const = receivers.functions[(owner, name)]
    for fn, readonly in ((mutable, False), (const, True)):
        call, = nodes(fn, th.THIRMethodCall)
        assert call.receiver_access.readonly is readonly
        loop, = nodes(fn, th.THIRForEach)
        assert loop.iteration.source.readonly is readonly


def test_a_const_local_alias_receiver_is_readonly(receivers: Program) -> None:
    # A local alias of a readonly parameter is emitted const; the loop over
    # the follows-receiver result walks it at that access.
    fn = receivers.fn(None, "const_alias")
    call, = nodes(fn, th.THIRMethodCall)
    assert call.receiver_access.readonly is True
    loop, = nodes(fn, th.THIRForEach)
    assert loop.iteration.source.readonly is True


def test_a_const_loop_variable_receiver_is_readonly(receivers: Program) -> None:
    fn = receivers.fn(None, "loop_var")
    call, = nodes(fn, th.THIRMethodCall)
    assert call.receiver_access.readonly is True
    # The inner loop walks the call result; the outer one, over the readonly
    # list parameter, carries no native iteration fact.
    facts = [loop.iteration for loop in nodes(fn, th.THIRForEach) if loop.iteration is not None]
    assert facts and all(fact.source.readonly for fact in facts)


def test_validator_holds_the_receiver_access_to_the_receiver(receivers: Program) -> None:
    fn = receivers.fn(None, "declared")
    validate_function(fn)
    call, = nodes(fn, th.THIRMethodCall)
    access = call.receiver_access
    with pytest.raises(THIRValidationError, match="receiver access disagrees with the receiver"):
        # A readonly[Bag] receiver is never emitted mutable.
        validate_function(_replace_node(fn, call, replace(call, receiver_access=replace(access, readonly=False))))
    with pytest.raises(THIRValidationError, match="receiver access disagrees with the receiver"):
        validate_function(_replace_node(fn, call, replace(call, receiver_access=replace(access, type=INT32))))
    with pytest.raises(THIRValidationError, match="receiver access needs exactly a resolved callee"):
        validate_function(_replace_node(fn, call, replace(call, receiver_access=None)))
    writer = receivers.fn(None, "mutable")
    push = next(c for c in nodes(writer, th.THIRMethodCall) if c.method_cpp == "push")
    with pytest.raises(THIRValidationError, match="receiver access needs exactly a resolved callee"):
        validate_function(_replace_node(writer, push, replace(push, resolved_callee=None)))
