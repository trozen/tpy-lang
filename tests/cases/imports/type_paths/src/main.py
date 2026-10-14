# A dotted path (`m.`, `mm.`, `pkg.sub.mod.`, from-import `MO.`, local) names
# its class or enum at every position, never the local twin or mx's own.
import asyncio
from enum import Enum
from typing import Callable, Final, Iterator, Protocol, cast

import m
import ma as mm
import mb
import mx
import pa
import pc
import pkg.sub.mod
from m import Outer as MO
from m import RErr
from tpy import Own, copy, dynamic, error_return, int32
import shadow_builtin


class Payload:
    def __init__(self, n: int) -> None:
        self.n = n


class Outer:
    # The local twin: same shape as m.Outer, different values.
    class Inner:
        v: int

        def __init__(self, v: int, w: int = 0) -> None:
            self.v = v + w + 1000

    class Holds:
        def __init__(self, p: Own[Payload]) -> None:
            self.p = p

    class Err(Exception):
        pass

    class Kind(Enum):
        A = 10
        B = 20

    NAME: Final[str] = "loc"
    N: Final[int32] = 3


# A top-level `Err` beside the local twin's nested one: `except Outer.Err`
# must not catch it.
class Err(Exception):
    pass


class KErr(Exception):
    def __init__(self, code: int) -> None:
        super().__init__("k")
        self.code = code


# annotation: parameters through every head; the callee's mutation is the
# caller's object
def bump(a: m.Outer.Inner, b: mm.Outer.Inner,  # tpyc: ok
         c: pkg.sub.mod.Outer.Inner, d: MO.Inner, e: Outer.Inner) -> None:
    a.v += 1
    b.v += 1
    c.v += 1
    d.v += 1
    e.v += 1


# annotation: return and generic argument
def first_kind(xs: list[m.Outer.Inner]) -> m.Outer.Kind:  # tpyc: ok
    return MO.Kind.B if xs[0].v > 1 else MO.Kind.A


# annotation: a generic class through a module head
def box_value(b: m.Box[int]) -> int:  # tpyc: ok
    b.v += 1
    return b.v


def kind_names(k: m.Outer.Kind, j: pkg.sub.mod.Outer.Kind, c: mm.Color,  # tpyc: ok
               t: Outer.Kind) -> str:
    return f"{k.name} {j.name} {c.name} {t.name}={t.value}"


# annotation: a field
class Holder:
    i: m.Outer.Inner  # tpyc: ok

    def __init__(self, v: int) -> None:
        self.i = m.Outer.Inner(v)


# annotation: base classes
class Sub(m.Outer.Inner):  # tpyc: ok
    pass


class SubP(pkg.sub.mod.Outer.Inner):  # tpyc: ok
    pass


# annotation: mx's own Outer.Inner is a different class
def mx_value(x: mx.Outer.Inner) -> int:  # tpyc: ok
    return x.v


def annotations() -> None:
    a = m.make_inner(1)
    b = mm.make_inner(2)
    c = pkg.sub.mod.Outer.Inner(3)
    d = m.make_inner(4)
    e = Outer.Inner(5)
    bump(a, b, c, d, e)
    print("annotation:", a.v, b.v, c.v, d.v, e.v)
    xs = [m.make_inner(2), m.make_inner(0)]
    print("return:", first_kind(xs).name)
    print("enums:", kind_names(MO.Kind.A, pkg.sub.mod.Outer.Kind.B, m.Color.RED, Outer.Kind.B))
    bx = m.box_of(3)
    print("generic:", box_value(bx), bx.v)
    h = Holder(6)
    h.i.v += 1
    print("field:", h.i.v)
    print("base:", Sub(7).v, SubP(8).v)
    print("mx:", mx_value(mx.Outer.Inner(9)))
    # annotation: a nested def's parameter and a local's declared type
    def inner(x: m.Outer.Inner) -> int:  # tpyc: ok
        x.v += 1
        return x.v
    y: m.Outer.Inner = m.Outer.Inner(10)  # tpyc: ok
    print("nested def:", inner(y), y.v)


# constructor and from_value through every head
def constructors() -> None:
    a = m.Outer.Inner(1)  # tpyc: ok
    b = mm.Outer.Inner(2)  # tpyc: ok
    c = pkg.sub.mod.Outer.Inner(3)  # tpyc: ok
    d = MO.Inner(4)  # tpyc: ok
    e = Outer.Inner(5)  # tpyc: ok
    bump(a, b, c, d, e)
    print("ctor:", a.v, b.v, c.v, d.v, e.v, mx.Outer.Inner(6).v)
    # a generic class through a module head, explicit and inferred
    print("ctor generic:", m.Box[int](7).v, m.Box(8).v)
    # keyword arguments reach the constructor's parameters
    print("ctor kwargs:", m.Outer.Inner(v=9).v, MO.Inner(v=10).v,  # tpyc: ok
          Outer.Inner(v=1).v, Outer.Inner(1, w=2).v)
    # an owned argument moves into a nested class's constructor
    p = Payload(3)
    h = Outer.Holds(p)  # tpyc: ok
    print("ctor own:", h.p.n)
    n = 10
    print("from_value:", m.Color(2).name, mm.Outer.Kind(1).name,  # tpyc: ok
          pkg.sub.mod.Outer.Kind(6).value, MO.Kind(2), Outer.Kind(n).name)


# members: enum members, class constants, iteration and lookup by name
def members() -> None:
    print("members:", m.Color.BLUE.name, mm.Outer.Kind.A.value,  # tpyc: ok
          pkg.sub.mod.Outer.Kind.B.name, MO.Kind.B, Outer.Kind.A.value)
    print("constants:", m.Outer.LIMIT, mm.Outer.Inner.K, MO.LIMIT, MO.Inner.K)  # tpyc: ok
    for k in m.Outer.Kind:  # tpyc: ok
        print("iter:", k.name)
    print("by name:", mm.Color["RED"].value)  # tpyc: ok


# isinstance and cast take a dotted class
def type_tests(o: m.Outer | m.Outer.Inner) -> None:
    print("isinstance:", isinstance(o, m.Outer.Inner),  # tpyc: ok
          isinstance(o, (mm.Outer, MO.Inner)),  # tpyc: ok
          isinstance(o, m.Outer | m.Outer.Inner))  # tpyc: ok
    o2 = m.Outer()
    # Read-only: the cast copies o2 where CPython aliases it
    # (BUGS.md#cast-live-name-copies).
    p = cast(m.Outer, o2)  # tpyc: ok
    print("cast:", p.LIMIT)


# a dotted class pattern; a dotted value pattern
def patterns(x: m.Outer.Inner | pkg.sub.mod.Outer.Inner, c: m.Color) -> None:
    match x:
        case m.Outer.Inner(v=1):  # tpyc: ok
            print("pattern: m one")
        case MO.Inner(v=w):
            print("pattern: m", w)
        case pkg.sub.mod.Outer.Inner(v=w):  # tpyc: ok
            print("pattern: mod", w)
    match c:
        case m.Color.RED:  # tpyc: ok
            print("value pattern: red")
        case mm.Color.BLUE:
            print("value pattern: blue")


# a class pattern with a local head
def local_pattern(o: Outer.Inner | m.Outer.Inner) -> None:
    match o:
        case Outer.Inner(v=1001):  # tpyc: ok
            print("local pattern: local")
        case m.Outer.Inner(v=1001):
            print("local pattern: m")
        case _:
            print("local pattern: other")


class Holds2:
    p: m.Outer.Inner | mx.Outer.Inner

    def __init__(self, p: m.Outer.Inner | mx.Outer.Inner) -> None:
        # the pattern reads the field only, so a copy is as good
        self.p = copy(p)


# a field class pattern compares the class, not its name: mx's Outer.Inner
# is not m's
def field_pattern(h: Holds2) -> None:
    match h:
        case Holds2(p=mx.Outer.Inner()):  # tpyc: ok
            print("field pattern: mx")
        case Holds2(p=m.Outer.Inner()):
            print("field pattern: m")
        case _:
            print("field pattern: other")


# a dotted class sub-pattern picks the generic instance
def generic_pattern(x: m.Box[m.A] | m.Box[m.B]) -> None:
    match x:
        case m.Box(v=m.A()):  # tpyc: ok
            print("generic pattern: A")
        case m.Box(v=m.B()):
            print("generic pattern: B")


class Holds3:
    b: m.Box[m.Outer.Inner] | m.Box[mx.Outer.Inner]

    def __init__(self, use_mx: bool) -> None:
        self.b = m.Box(m.Outer.Inner(1))
        if use_mx:
            self.b = m.Box(mx.Outer.Inner(2))


# a generic class over two same-named classes: one arm per instance, both
# reached; the binding aliases the field
def same_name_generic(h: Holds3) -> None:
    b = h.b
    match b:
        case m.Box(v=mx.Outer.Inner() as i):  # tpyc: ok
            i.v += 1
        case m.Box(v=m.Outer.Inner() as i):  # tpyc: ok
            i.v += 10
    c = h.b
    match c:
        case m.Box(v=mx.Outer.Inner() as i):
            print("same-name generic: mx", i.v)
        case m.Box(v=m.Outer.Inner() as i):
            print("same-name generic: m", i.v)


# isinstance tells two same-named classes apart
def same_name_isinstance(h: Holds2) -> None:
    p = h.p
    if isinstance(p, mx.Outer.Inner):  # tpyc: ok
        print("same-name isinstance: mx", p.v)
    elif isinstance(p, m.Outer.Inner):  # tpyc: ok
        print("same-name isinstance: m", p.v)


class Holds4:
    b: m.Box[int] | str

    def __init__(self, n: int) -> None:
        self.b = m.Box(n)


# isinstance with a bare generic class selects its instance
def bare_generic_isinstance(h: Holds4) -> None:
    b = h.b
    if isinstance(b, m.Box):  # tpyc: ok
        b.v += 1
    c = h.b
    if isinstance(c, m.Box):
        print("bare generic isinstance:", c.v)


# a dotted class at a Callable slot is its constructor
def build(f: Callable[[int], m.Outer.Inner], v: int) -> int:
    i = f(v)
    i.v += 1
    return i.v


# defaults: an enum member or a Final class constant through every head
def defaults(k: m.Outer.Kind = m.Outer.Kind.B, c: mm.Color = mm.Color.BLUE,  # tpyc: ok
             n: int32 = MO.LIMIT,
             j: pkg.sub.mod.Outer.Kind = pkg.sub.mod.Outer.Kind.A) -> str:
    return f"{k.name} {c.name} {n} {j.name}"


# defaults: a class constant of this module, whose class the prototype needs
def local_defaults(s: str = Outer.NAME, n: int32 = Outer.N) -> str:  # tpyc: ok
    return f"{s} {n}"


# a signature naming a nested class of this module
def local_inner(i: Outer.Inner) -> int:  # tpyc: ok
    return i.v


class Wrap[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    # a generic class's inline body calls both functions above: their
    # prototypes must precede this class, after Outer
    def call(self) -> str:
        return f"{local_defaults()} {local_inner(Outer.Inner(5))}"  # tpyc: ok


class Config:
    # a field default through a module head
    color: m.Color = m.Color.BLUE  # tpyc: ok
    # a str field default from a str class constant
    label: str = Outer.NAME  # tpyc: ok

    class Mode(Enum):
        X = 1
        Y = 2

    # a default whose head is a class nested in this class body
    def pick(self, mode: Mode = Mode.Y) -> int:  # tpyc: ok
        return mode.value


# a bare builtin class at a Callable slot is its constructor
def mk(f: Callable[[str], ValueError], s: str) -> str:
    e = f(s)
    return "made " + s


class Leaf:
    v: int

    def __init__(self) -> None:
        self.v = 1


class Tree:
    class Leaf:
        v: int

        def __init__(self) -> None:
            self.v = 2

    # a method body names the module's Leaf, not the nested one: CPython
    # method bodies skip the class scope
    def make(self) -> int:
        return Leaf().v  # tpyc: ok


# a parameter named like the module shadows it in the body; the annotation
# and the default still resolve in the defining (module) scope
def shadow(m: int, k: m.Outer.Kind = m.Outer.Kind.A) -> str:  # tpyc: ok
    return f"{m + 1} {k.name}"


class Factory:
    # constructor in a method
    def make(self, v: int) -> int:
        i = m.Outer.Inner(v)  # tpyc: ok
        return i.v


# constructor in a generator
def gen(n: int) -> Iterator[int]:
    for i in range(n):
        yield mm.Outer.Inner(i).v  # tpyc: ok


def positions() -> None:
    print("method:", Factory().make(3))
    print("comprehension:", [pkg.sub.mod.Outer.Inner(i).v for i in range(2)])  # tpyc: ok
    print("generator:", list(gen(2)))
    f: Callable[[int], int] = lambda v: MO.Inner(v).v  # tpyc: ok
    print("lambda:", f(5))
    k = m.Outer.Kind.B
    match k:
        case m.Outer.Kind.A:
            print("arm: a")
        case _:
            print("arm:", m.Outer.Inner(9).v, m.Outer.Kind(1).name)  # tpyc: ok
    # a loop over a list[str] binds `std::string_view s` when its body names
    # an enum through a module path: the path reads no storage
    xs = ["p", "q"]
    for s in xs:
        print("loop:", s, m.Color.RED == m.Color(1))  # tpyc: ok


# a module-qualified protocol parent and type-parameter bound
class Doubles(m.HasV, Protocol):  # tpyc: ok
    def twice(self) -> int: ...


class Probe:
    def __init__(self, v: int) -> None:
        self.v = v

    def get_v(self) -> int:
        return self.v

    def twice(self) -> int:
        return self.v * 2


def total[T: m.HasV](x: T) -> int:  # tpyc: ok
    return x.get_v()


def both[T: Doubles](x: T) -> int:
    return x.get_v() + x.twice()


def protocols() -> None:
    print("protocol:", total(Probe(4)), both(Probe(5)))


# Two same-named protocols in two modules are two protocols: a pa.C reaches
# pc.P through an adapter, and the callee's mutation is the caller's object.
def use_pc(x: pc.P) -> int:  # tpyc: ok
    x.bump()
    return x.value()


# A local protocol named like pa.P: a protocol extending pa.P requires
# pa.P's methods, not the local one's.
@dynamic
class P(Protocol):
    def other(self) -> int: ...


@dynamic
class Q(pa.P, Protocol):  # tpyc: ok
    def extra(self) -> int: ...


class D(Q):
    v: int

    def __init__(self) -> None:
        self.v = 5

    def value(self) -> int:
        return self.v

    def bump(self) -> None:
        self.v += 1

    def extra(self) -> int:
        return 6


def use_q(q: Q) -> int:
    return q.value() + q.extra()


def same_name_protocols() -> None:
    c = pa.C(3)
    print("protocol identity:", use_pc(c), c.v)
    d = D()
    print("protocol parent:", use_q(d), use_pc(d), d.v)


# Module-level twins of the classes `Shade` nests. A method body names these:
# CPython method bodies skip the class scope.
class Color(Enum):
    RED = 1
    BLUE = 2


class Kind(Enum):
    A = 1
    B = 2


class LErr(Exception):
    pass


class Cfg:
    N: Final[int32] = 5

    @staticmethod
    def make() -> int:
        return 6


class Shade:
    class Color(Enum):
        RED = 10
        BLUE = 20

    class Kind(Enum):
        A = 10
        B = 20

    class LErr(Exception):
        pass

    class Cfg:
        N: Final[int32] = 50

        @staticmethod
        def make() -> int:
            return 60

    def run(self) -> None:
        # the module's Color, Kind, LErr and Cfg, not the nested twins
        print("shade:", Color.BLUE.value, Color(1).name, Cfg.N, Cfg.make())  # tpyc: ok
        x = Kind.B  # tpyc: ok
        print("shade:", x.value)
        try:
            raise LErr()  # tpyc: ok
        except Shade.LErr:
            print("shade: wrong nested")
        except LErr:  # tpyc: ok
            print("shade: module")
        try:
            raise Shade.LErr()
        except LErr:
            print("shade: wrong module")
        except Shade.LErr:
            print("shade: nested")


# except / raise through every head
def exceptions() -> None:
    try:
        raise m.Outer.Err("x")  # tpyc: ok
    except mm.Outer.Err:  # tpyc: ok
        print("except: m nested")
    try:
        raise mx.Err  # tpyc: ok
    except mx.Outer.Err:
        print("except: wrong")
    except mx.Err:  # tpyc: ok
        print("except: mx flat")
    try:
        raise mx.Outer.Err()
    except (KeyError, mx.Outer.Err):  # tpyc: ok
        print("except: tuple")
    try:
        raise Err()
    except Outer.Err:  # tpyc: ok
        print("except: wrong local")
    except Err:
        print("except: local flat")
    try:
        raise Outer.Err()  # tpyc: ok
    except Outer.Err:
        print("except: local nested")
    try:
        raise MO.Err("y")
    except Exception:
        print("except: from-import nested")
    # mx's own TimeoutError, not the builtin
    try:
        raise mx.TimeoutError()
    except TimeoutError:
        print("except: wrong builtin")
    except mx.TimeoutError:  # tpyc: ok
        print("except: module shadows builtin")
    # a dotted raise with keyword arguments constructs the exception
    try:
        raise m.Err(code=3)  # tpyc: ok
    except m.Err as e:
        print("except: keyword", e.code)
    # a bare raise with keyword arguments constructs it the same way
    try:
        raise KErr(code=4)  # tpyc: ok
    except KErr as e:
        print("except: bare keyword", e.code)


# a return-tier exception through a module path in an @error_return body
@error_return(RErr)
def checked(n: int) -> int:
    if n < 0:
        raise m.RErr()  # tpyc: ok
    return n


def error_returns() -> None:
    try:
        v = checked(-1)
    except m.RErr as e:  # tpyc: ok
        print("error_return: caught")
    else:
        print("error_return:", v)


# a dotted except clause in an async body whose handler awaits
async def async_except() -> None:
    try:
        raise m.Outer.Err("a")
    except m.Outer.Err:  # tpyc: ok
        await asyncio.sleep(0)
        print("async: caught")


def main() -> None:
    annotations()
    constructors()
    members()
    t = m.Outer.Inner(1)
    type_tests(t)
    i = pkg.sub.mod.Outer.Inner(2)
    patterns(i, m.Color.BLUE)
    j = m.Outer.Inner(1)
    patterns(j, m.Color.RED)
    l1 = Outer.Inner(1)
    local_pattern(l1)
    l2 = m.Outer.Inner(1001)
    local_pattern(l2)
    f1 = m.make_inner(1)
    field_pattern(Holds2(f1))
    f2 = mx.make_inner(1)
    field_pattern(Holds2(f2))
    ga = m.Box(m.A(1))
    generic_pattern(ga)
    gb = m.Box(m.B(2))
    generic_pattern(gb)
    print("local defaults:", local_defaults(), local_defaults("x", 4))
    print("field str default:", Config().label)
    print("generic caller:", Wrap(1).call())
    protocols()
    error_returns()
    asyncio.run(async_except())
    print("factory:", build(m.Outer.Inner, 4), build(mm.Outer.Inner, 5),  # tpyc: ok
          build(MO.Inner, 6))
    print("defaults:", defaults(), defaults(MO.Kind.A, n=3))
    print("field default:", Config().color.name, Config().pick())
    print("shadow:", shadow(1))
    positions()
    exceptions()
    same_name_generic(Holds3(False))
    same_name_generic(Holds3(True))
    s1 = m.make_inner(3)
    same_name_isinstance(Holds2(s1))
    s2 = mx.make_inner(3)
    same_name_isinstance(Holds2(s2))
    bare_generic_isinstance(Holds4(5))
    print("callable builtin:", mk(ValueError, "boom"))  # tpyc: ok
    print("make:", Tree().make())
    print("same-name base:", mb.Outer.Inner(1).v)
    shadow_builtin.run()
    same_name_protocols()
    Shade().run()


# module level
print("module:", m.Outer.Inner(11).v, m.Color.RED.name, mm.Outer.LIMIT)  # tpyc: ok
main()
