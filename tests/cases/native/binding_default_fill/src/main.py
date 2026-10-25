# A call omitting an argument of a binding (@native, @cpp_template, a native
# class's stub, @export(binding="C")) passes the TPy signature's default.
__tpy__ = {"includes": ["native_types.hpp"]}
import asyncio
from enum import Enum, auto
from typing import Callable, Final, Iterator, Protocol, overload
from tpy import int32, int64, dispatch, readonly
from tpy.extern import native, cpp_template, export
import nbind
from nbind import limited, add2, lim

MAXP: Final[int64] = 99
K: Final[int32] = 9


@native("nb::E")
class E(Enum):
    A = auto()
    B = auto()


@native("nb::Q")
class Q:
    @native("emplace_back")
    def add(self, e: E, p: int64 = MAXP, n: int32 = 0) -> None: ...
    @readonly
    def last_p(self) -> int64: ...
    @readonly
    def last_n(self) -> int32: ...


@native("nb::RD")
class RD:
    def __init__(self, b: int32, k: int32 = 7) -> None: ...
    def get(self, n: int32 = 5) -> int32: ...
    @native("nb::ff", function=True)
    def ff(self, n: int32 = 5) -> int32: ...


@native("nb::GBox")
class GBox[T]:
    def __init__(self, x: T, y: T = T()) -> None: ...
    def get(self) -> T: ...


@native("nb::GM")
class GM:
    def pick[T](self, x: T, y: T = T()) -> T: ...


@native("nb::DM")
class DM:
    @dispatch
    @native("f")
    def f(self, x: int32, k: int32 = 5) -> int32: ...

    @dispatch
    @native("f")
    def f(self, s: str, k: int32 = 6) -> int32: ...


@native("nb::OV")
class OV:
    @overload
    def f(self, x: int32 = 5) -> int32: ...
    @overload
    def f(self, x: str) -> int32: ...
    def f(self, x: int32 | str = 5) -> int32: ...


@native("nb::PBag")
class PBag[T]:
    k: int32

    def __init__(self, kk: int32 = 5) -> None: ...
    def push(self, v: T) -> None: ...
    def tag(self, n: int32 = 5) -> int32: ...


class GBox1[W](GBox[W]):
    pass


class GBox2[V](GBox1[V]):
    pass


@native("nb::GEx")
class GEx[T](Exception):
    def __init__(self, v: T, k: int32 = 5) -> None: ...


@native("nb::GErr")
class GErr(Exception):
    @dispatch
    @cpp_template("nb::GErr({0}, {1})")
    def __init__(self, a: int32, b: int32 = 5) -> None: ...

    @dispatch
    @cpp_template("nb::GErr({0}, {1})")
    def __init__(self, s: str, k: int32 = 6) -> None: ...


@native("nb::LErr")
class LErr(Exception):
    @dispatch
    @cpp_template("nb::LErr({0})")
    def __init__(self, fn: Callable[[int32], int32]) -> None: ...

    @dispatch
    @cpp_template("nb::LErr({0})")
    def __init__(self, n: int32) -> None: ...


@native("nb::VErr")
class VErr(Exception):
    @dispatch
    @cpp_template("nb::VErr({0})")
    def __init__(self, value: Callable[[int32], int32]) -> None: ...

    @dispatch
    @cpp_template("nb::VErr({0})")
    def __init__(self, value: int32) -> None: ...


@native("nb::KErr")
class KErr(Exception):
    @dispatch
    @cpp_template("nb::KErr({0}, {1})")
    def __init__(self, x: str, *, k: int32 = 1) -> None: ...

    @dispatch
    @cpp_template("nb::KErr({0}, {1})")
    def __init__[T](self, x: list[T], *, k: int32 = 2) -> None: ...


@native("nb::cppdef")
def cppdef(x: int32, y: int32 = 5) -> int32: ...


@native("nb::tagged")
def tagged(x: int32, t: str = "tpy") -> int32: ...


@native("nb::moded")
def moded(x: int32, e: E = E.B) -> int32: ...


@native("nb::sized")
def sized(x: int32, d: bytes = b"xy") -> int32: ...


@native("nb::generic")
def add_default[T](x: T, y: T = T()) -> T: ...


@native("nb::nf")
def nf(a: int32, *rest: int32, k: int32 = 7) -> int32: ...


@native("nb::total")
def total(*xs: int32, k: int32 = 5) -> int32: ...


@native("nb::w")
def w(x: int32, *, k: int32 = 5) -> int32: ...


@native("nb::add3")
def add3[T](x: T, y: T = T(), z: T = T()) -> T: ...


@native("nb::joined")
def joined[T](x: T, y: T = T()) -> T: ...


@native("nb_cfn", binding="C")
def cfn(x: int32, y: int32 = 5) -> int32: ...


@export(binding="C")
def ec(a: int32, b: int32 = 5) -> int32:
    return a * 1000 + b


class P(Protocol):
    def step(self, x: int32 = 7) -> int32: ...


class S:
    def __init__(self) -> None:
        pass

    def step(self, x: int32 = 3) -> int32:
        return x


def through(p: P) -> int32:
    # Inverse: a protocol call takes its TPy conformer's own default.
    return p.step()  # tpyc: ok


def tpy_def(x: int32, y: int32 = 5) -> int32:
    return x * 1000 + y


class User:
    v: int32

    def __init__(self) -> None:
        # Position: constructor body.
        self.v = cppdef(3)  # tpyc: ok

    def run(self, q: Q) -> None:
        # Position: method body.
        q.add(E.A)  # tpyc: ok
        print("pos-method", q.last_p(), q.last_n(), cppdef(2))


def gen(q: Q) -> Iterator[int32]:
    # Position: generator body.
    q.add(E.B, p=4)  # tpyc: ok
    yield cppdef(5)  # tpyc: ok


async def coro(q: Q) -> int32:
    await asyncio.sleep(0)
    # Position: async body.
    q.add(E.A, p=6)  # tpyc: ok
    return cppdef(6)  # tpyc: ok


def main() -> None:
    q = Q()
    # Callee kind: @native method (method-style name), keywords and a gap.
    q.add(e=E.A, p=3)  # tpyc: ok
    print("method", q.last_p(), q.last_n())
    q.add(E.A, n=5)  # tpyc: ok
    print("method-gap", q.last_p(), q.last_n())
    # Default reading this module's Final constant.
    q.add(E.B)  # tpyc: ok
    print("final-own", q.last_p(), q.last_n())
    # Callee kind: free @native function.
    print("free", cppdef(1))  # tpyc: ok
    # Callee kind: @native(binding="C") function.
    print("c-binding", cfn(1))  # tpyc: ok
    # Callee kind: @export(binding="C") function called from TPy.
    print("export-c", ec(1))  # tpyc: ok
    # Callee kind: native class ctor stub, and a method stub.
    r = RD(1)  # tpyc: ok
    print("ctor", r.get())  # tpyc: ok
    # Callee kind: @native(function=True) method.
    print("fn-method", r.ff())  # tpyc: ok
    # Callee kind: native ctor group, each variant filled against itself,
    # constructed and raised.
    print("group", str(GErr(1)), str(GErr("x")))  # tpyc: ok
    # A keyword lambda at a constructor group: typed by each candidate.
    le = LErr(fn=lambda x: x + 1)  # tpyc: ok
    print("group-kw-lambda", str(le))
    # A keyword int at a constructor group whose overloads share the name:
    # the callable candidate refuses it, the int one takes it.
    ve = VErr(value=3)  # tpyc: ok
    print("group-kw-shared-name", str(ve))
    # A keyword picks the constructor variant, the positional and the
    # keyword spelling alike, and a generic variant's type argument is
    # inferred from a keyword.
    ka = KErr("a", k=3)  # tpyc: ok
    kb = KErr([1, 2], k=3)  # tpyc: ok
    kc = KErr(x=[1, 2, 3])  # tpyc: ok
    print("group-kw-generic", str(ka), str(kb), str(kc))
    try:
        raise GErr("y")  # tpyc: ok
    except GErr as g:
        print("group-raise", str(g))
    # Callee kind: @cpp_template referencing the omitted argument.
    print("template", add2(1))  # tpyc: ok
    print("template-qualified", nbind.add2(1))  # tpyc: ok
    # Default naming another module's Final constant.
    print("final-other", limited(1), nbind.limited(2))  # tpyc: ok
    # Default shapes: str literal at a view param, enum member, bytes.
    print("str", tagged(1))  # tpyc: ok
    print("enum", moded(1))  # tpyc: ok
    print("bytes", sized(1))  # tpyc: ok
    # A generic binding's `T()` default constructs the bound type.
    print("generic", add_default[int32](3))  # tpyc: ok
    # Same, the type argument inferred from a keyword argument.
    print("generic-kw", add_default(x=int32(4)))  # tpyc: ok
    # Same, through the module-qualified spelling.
    print("generic-qualified", nbind.gadd(int32(2)))  # tpyc: ok
    # Same, a generic native class's ctor and a generic native method.
    print("generic-ctor", GBox[int32](3).get())  # tpyc: ok
    print("generic-method", GM().pick(int32(2)))  # tpyc: ok
    # Callee kind: a @dispatch native method group, filled against the winner.
    dm = DM()
    print("dispatch-method", dm.f(1), dm.f("ab"))  # tpyc: ok
    # Callee kind: a typing.overload stub on a native class.
    print("overload-stub", OV().f())  # tpyc: ok
    # Callee kind: the ctor of a generic native class whose type argument a
    # later call infers.
    pb = PBag()  # tpyc: ok
    pb.push(int32(1))
    print("pending-ctor", pb.k)
    # A `*args` binding's keyword-only slot is passed once, after the pack.
    print("varargs-kwonly", nf(1), nf(1, 2, 3), total(1, 2), total(1, 2, k=5))  # tpyc: ok
    # Callee kind: a keyword-only binding parameter.
    print("kwonly", w(1), w(1, k=2))  # tpyc: ok
    # Keyword arguments to a non-generic binding and a native ctor stub.
    print("keyword", cppdef(x=1), RD(b=1).get())  # tpyc: ok
    # A generic binding's `T()` gap before a keyword argument.
    print("generic-gap", add3[int32](1, z=5))  # tpyc: ok
    # A generic native class's ctor, its type argument inferred.
    print("generic-ctor-inferred", GBox(3).get())  # tpyc: ok
    # The ancestor's `T()` default of a ctor inherited over two generic levels.
    print("inherited-two-level", GBox2[int32](3).get())  # tpyc: ok
    # A `T()` default bound to a `str` local.
    s: str = "ab"
    print("generic-str", joined(s))  # tpyc: ok
    # A method of a generic native class whose type argument is still pending.
    pb2 = PBag()
    print("pending-method", pb2.tag())  # tpyc: ok
    pb2.push(2)
    # A raised generic native exception, its type argument inferred.
    try:
        raise GEx(3)  # tpyc: ok
    except Exception as gx:
        print("generic-raise", str(gx))
    # Inverse: a protocol call over a TPy conformer.
    print("protocol", through(S()))
    # A filled `int32(K)` reads the callee module's K, not this module's
    # constant nor a local of the same name.
    K = 99
    print("filled-call-default", lim(1), K)  # tpyc: ok
    # Inverse: a TPy function keeps its C++ default argument.
    print("tpy-default", tpy_def(1))  # tpyc: ok
    User().run(q)
    print("pos-ctor", User().v)
    for v in gen(q):
        print("pos-generator", v, q.last_p(), q.last_n())
    print("pos-async", asyncio.run(coro(q)), q.last_p(), q.last_n())
    # Position: comprehension.
    print("pos-comprehension", [cppdef(i) for i in range(2)])  # tpyc: ok
    # Position: lambda.
    f: Callable[[], int32] = lambda: cppdef(7)  # tpyc: ok
    print("pos-lambda", f())


main()
# Position: module-level statement.
print("pos-module", cppdef(4), RD(4).get())  # tpyc: ok
print("filled-call-default-module", lim(1))  # tpyc: ok
