# An omitted parameter default takes the value it names where its def is
# written (CPython binds it once, at the def), whatever the calling scope
# binds the same names to. Every call below omits a default before an
# argument it passes, so the default is filled at the call.
import asyncio
import enum
from dataclasses import dataclass
from typing import Callable, Final, Iterator
from tpy import int32, StrView
import defs
from defs import f, fp, fw, fe, fx, fm, d, ov, view, gen, Made, MadeLater
import matching
from reexported import MadeReexport

# This module's own constants, named like the ones the defaults name.
K: Final[int32] = 1000
_K: Final[int32] = 1000
S: Final[str] = "shadow"
LOCAL: Final[int32] = 4


class E(enum.Enum):
    A = 1
    B = 5


class C:
    X: Final[int32] = 99
    V: Final[int32] = 98
    K: Final[int32] = 97


class D(defs.C):
    pass


# A dataclass extending one from defs: its `__init__` takes the parent's
# field default as written there.
# Its warnings hold for any dataclass child: TPy dispatches methods statically
# and default-constructs the parent part, where CPython dispatches dynamically.
@dataclass
class SubRec(defs.Rec):  # tpyc: warning(/hides 'Rec.__eq__'/) warning(/hides 'Rec.__repr__'/) warning(/does not call 'Rec.__init__'/)
    m: int32 = 0


class CM:
    def __enter__(self) -> int32:
        return 1000

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def lf(a: int32, b: int32 = LOCAL, c: int32 = 0) -> int32:
    return a * 100 + b * 10 + c


class Runner:
    v: int32

    # constructor position
    def __init__(self, K: int32) -> None:
        # the default's K, not the parameter
        self.v = f(1, c=9)  # tpyc: ok

    # method position
    def run(self, K: int32) -> int32:
        return f(1, c=9)  # tpyc: ok


# A protocol method's default, resolved where the protocol is written.
def through_protocol(p: defs.P) -> int32:
    return p.step(y=9)  # tpyc: ok


# caller parameter named like the default's constant
def param(K: int32) -> int32:
    return f(1, c=9)  # tpyc: ok


# caller parameter named like the constructor in `fw`'s `int64(K)` default
def ctor_param(int64: int32) -> None:
    print("int ctor shadowed", fw(c=int64))  # tpyc: ok


# A function named like the class a dataclass field's default_factory
# names where the dataclass is written.
def Pt() -> int32:
    return 99


# caller parameter named like the field's other factory, `dict`
def factory_shadowed(dict: int32) -> None:
    m = Made(c=dict)  # tpyc: ok
    # The filled default is the object the field holds, not a copy of it.
    m.p.x += 1
    m.ys[1] = 2
    print("factory shadowed", m.p.x, len(m.ys), m.c, Pt())


# caller parameter named like a factory re-exported through a helper module
def reexport_shadowed(list: int32) -> None:
    m = MadeReexport(n=list)  # tpyc: ok
    m.xs.append(1)
    print("reexported factory shadowed", len(m.xs), m.n)


# `with ... as` capture named like the default's constant
def capture() -> int32:
    with CM() as K:
        return f(1, c=9)  # tpyc: ok


# a same-module default under a same-module capture of its name
def capture_local() -> int32:
    with CM() as LOCAL:
        return lf(1, c=9)  # tpyc: ok


# generator position
def gen_pos(K: int32) -> Iterator[int32]:
    yield f(1, c=9)  # tpyc: ok


# async position
async def async_pos(K: int32) -> int32:
    return f(1, c=9)  # tpyc: ok


# A Final str default returned as a view through this module's own view
# function.
def through_view() -> StrView:
    return view(n=1)  # tpyc: ok


# The class constant a function returns off an instance outlives the
# instance.
def static_recv() -> StrView:
    c = defs.C()
    return c.S  # tpyc: ok


def static_recv_call() -> StrView:
    return defs.read(defs.C())  # tpyc: ok


# A Final class constant read off a temporary receiver is the class's
# storage, which outlives the temporary.
def static_temp() -> StrView:
    return defs.C().S  # tpyc: ok


def static_temp_generic[T](v: T) -> StrView:
    return defs.Box(v).F  # tpyc: ok


def relay(xs: Iterator[defs.C]) -> Iterator[StrView]:
    for c in xs:
        # the class's storage, not the step's
        yield c.S  # tpyc: ok


def source() -> Iterator[defs.C]:
    c = defs.C()
    yield c


def main(flag: bool) -> None:
    # free function
    print("free", f(1, c=9))  # tpyc: ok
    print("free trailing", f(1))  # tpyc: ok
    print("private", fp(c=9))  # tpyc: ok
    print("int ctor", fw(c=9))  # tpyc: ok
    print("enum", fe(c=9))  # tpyc: ok
    print("class const", fx(c=9))  # tpyc: ok
    print("module qualified", fm(c=9))  # tpyc: ok
    print("dispatch", d(1, y=9))  # tpyc: ok
    print("overload", ov(1, y=9))  # tpyc: ok
    # methods and constructors: the class body's constants
    c = defs.C(b=9)  # tpyc: ok
    print("ctor", c.n)
    print("inherited ctor", D(b=9).n)  # tpyc: ok
    print("dataclass child", SubRec(m=1).n)  # tpyc: ok
    print("method", c.m(y=9))  # tpyc: ok
    print("classmethod", defs.C.cm(y=9))  # tpyc: ok
    print("staticmethod", defs.C.sm(y=9))  # tpyc: ok
    print("generic method", defs.G[int32]().g(1, y=9))  # tpyc: ok
    # A class constant declared after the def is not the default's; the
    # trailing call takes the C++ default argument inside that class.
    print("class order", defs.Late().m(y=9), defs.Late().m())  # tpyc: ok
    # A base's constant and an earlier parameter of the same name are not
    # the default's, in the C++ default argument either.
    print("inherited name", defs.Derived().m2(y=9), defs.Derived().m2())  # tpyc: ok
    print("parameter name", defs.shadowed(1))  # tpyc: ok
    # A dotted default whose head the class body nests only after the def
    # names the module's class or enum, filled and as the C++ default.
    print("dotted class order", defs.LateDotted().m(y=9), defs.LateDotted().m())  # tpyc: ok
    print("dotted enum order", defs.LateDotted().e(y=9), defs.LateDotted().e())  # tpyc: ok
    print("dotted field order", defs.LateRec().n)  # tpyc: ok
    # An instance field named like the default's constant is not it.
    print("instance field name", defs.W().m(y=9), defs.W().m(), defs.W().K)  # tpyc: ok
    # A field the class body binds after the def, or only annotates
    # (`W.K` above), is not the default's.
    print("field order", defs.LateField().m(y=9), defs.LateField().m(), defs.LateField().K)  # tpyc: ok
    late = MadeLater(list=4)  # tpyc: ok
    late.xs.append(1)
    fresh = MadeLater()  # tpyc: ok
    print("factory order", len(late.xs), late.list, len(fresh.xs), fresh.list)
    print("classvar", defs.fcv(c=9), defs.fcv())  # tpyc: ok
    print("protocol", through_protocol(defs.Stepper()))
    print("param", param(5))
    ctor_param(8)
    # A constructor callee the class body binds only after the def is not
    # the default's.
    print("class ctor order", defs.LateCtor().m(y=9), defs.LateCtor().m())  # tpyc: ok
    factory_shadowed(3)
    # A default_factory re-exported through a helper module, from a caller
    # naming the same class.
    mr = MadeReexport(n=2)  # tpyc: ok
    mr.xs.append(1)
    print("reexported factory", len(mr.xs), mr.n)
    reexport_shadowed(3)
    print("capture", capture())
    print("capture same module", capture_local())
    r = Runner(5)
    print("ctor position", r.v)
    print("method position", r.run(5))
    for v in gen_pos(5):
        print("generator", v)
    print("async", asyncio.run(async_pos(5)))
    # comprehension variable named like the default's constant
    print("comprehension", [f(1, c=K) for K in [8, 9]])  # tpyc: ok
    # lambda parameter named like the default's constant
    fn: Callable[[int32], int32] = lambda K: f(1, c=K)  # tpyc: ok
    print("lambda", fn(9))
    print("view", through_view())
    # a generator declared in front of the `if`, retaining a str default
    if flag:
        g = gen(tail=9)  # tpyc: ok
    else:
        g = gen(tail=8)
    for s in g:
        print("hoisted generator", s)
    print("static receiver", static_recv(), static_recv_call())
    print("static temporary", static_temp(), static_temp_generic(4))
    for s in relay(source()):
        print("static receiver yield", s)
    matching.run()
    print("own", K, _K, S, E.B.value, C.X, C.V, C.K)


# module level, a same-module default
print("module level", f(1, c=9), lf(1, c=9))  # tpyc: ok
main(True)
