# A numeric literal ranks on one price scale from its home (default int width,
# `float`) and renders as the picked parameter's type, so C++ binds sema's pick.
import math
from typing import Callable, Iterator, overload
from tpy import dispatch, float32, int16, int32, int64, Hashable


class Animal:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


# Every set declares the compiler's winner first: the CPython `@dispatch`
# stand-in runs the first applicable variant (any order: dispatch_rank_declared_shape_order).
@dispatch
def num(x: int) -> str:
    return "int"


@dispatch
def num(x: float) -> str:
    return "float"


@dispatch
def width(x: float) -> str:
    return "f64"


@dispatch
def width(x: float32) -> str:
    return "f32"


@dispatch
def mixed(x: float, a: Animal) -> str:
    a.hits += 1
    return "f64_animal"


@dispatch
def mixed(x: float32, a: Dog) -> str:
    a.hits += 10
    return "f32_dog"


@dispatch
def exact(x: int) -> str:
    return "int"


@dispatch
def exact(x: float32) -> str:
    return "f32"


@dispatch
def narrow(x: int16) -> str:
    return "int16"


@dispatch
def narrow(x: int64) -> str:
    return "int64"


@overload
def stub(x: int) -> int: ...
@overload
def stub(x: float) -> int: ...
def stub(x: int | float) -> int:
    if isinstance(x, int):
        return 1
    return 3


class V:
    def __init__(self) -> None:
        pass

    @dispatch
    def mm(self, x: int) -> int:
        return x * 2

    @dispatch
    def mm(self, x: float) -> float:
        return x / 4

    @dispatch
    def opt(self, x: int | None) -> str:
        return "intopt"

    @dispatch
    def opt(self, x: float | None) -> str:
        return "floatopt"

    @dispatch
    def fopt(self, x: float | None) -> str:
        return "f64opt"

    @dispatch
    def fopt(self, x: float32) -> str:
        return "f32"

    @dispatch
    def iopt(self, x: int32 | None) -> str:
        return "int32opt"

    @dispatch
    def iopt(self, x: int64) -> str:
        return "int64"

    @dispatch
    def nopt(self, x: int16 | None) -> str:
        return "int16opt"

    @dispatch
    def nopt(self, x: int64) -> str:
        return "int64"

    @dispatch
    def narrow(self, x: int16) -> str:
        return "int16"

    @dispatch
    def narrow(self, x: int64) -> str:
        return "int64"

    @dispatch
    def mv(self, x: bool) -> int:
        return 2

    @dispatch
    def mv(self, x: int) -> int:
        return 3

    @dispatch
    def width(self, x: float) -> str:
        return "f64"

    @dispatch
    def width(self, x: float32) -> str:
        return "f32"

    @dispatch
    def f(self, x: float32) -> float:
        # TPy holds a float32 already; the stand-in rounds here.
        return float32(x)

    @dispatch
    def f(self, x: str) -> float:
        return 0.0

    @dispatch
    def fo(self, x: float | None) -> str:
        return "fopt"

    @dispatch
    def fo(self, x: str) -> str:
        return "str"

    @dispatch
    def wide(self, x: int32) -> str:
        return "int32"

    @dispatch
    def wide(self, x: int64) -> str:
        return "int64"


def take32(x: float32) -> float:
    return float(x)


@dispatch
def p(x: int64) -> str:
    return "int64"


@dispatch
def p(x: Hashable) -> str:
    return "Hashable"


@dispatch
def spec(x: int64, a: Dog) -> str:
    a.hits += 10
    return "dog"


@dispatch
def spec(x: int32, a: Animal) -> str:
    a.hits += 1
    return "animal"


@dispatch
def pick(x: int64, y: int64, z: int64) -> str:
    return "fixed"


@dispatch
def pick(x: int32, y: int32, z: int) -> str:
    return "big"


@dispatch
def amb(x: int64, y: int64) -> str:
    return "wide"


@dispatch
def amb(x: int32, y: int) -> str:
    return "mixed"


@dispatch
def big(x: int64) -> str:
    return "int64"


@dispatch
def big(x: int) -> str:
    return "int"


def ticked(calls: list[int32]) -> bool:
    calls.append(1)
    return True


def free_function() -> None:
    # An int literal is exact at `int`'s default width and widens less to
    # `int` than to `float`; a float literal is at home at `float`.
    print("free_function:", num(3), num(0.5), num(3.0))  # tpyc: ok
    print("free_function:", width(0.25), exact(16777217), narrow(3))  # tpyc: ok
    d = Dog()
    # The float literal is exact at `float`, so the base variant wins.
    print("free_function:", mixed(0.1, d), d.hits)  # tpyc: ok
    ys: list[int] = [2, 3]
    # An int literal at the int `start` of the int / float prod pair.
    print("free_function:", math.prod(ys, start=2))  # tpyc: ok
    n: int = 2
    # A BigInt value at a plain float32 parameter converts through double.
    print("free_function:", take32(n), take32(n + 5))  # tpyc: ok


def overload_stubs() -> None:
    # The `int` stub wins for an int literal; the implementation sees an int.
    print("overload_stubs:", stub(1), stub(1.5))  # tpyc: ok


def method() -> None:
    v = V()
    # The variants return different types: the int one runs.
    r = v.mm(3)  # tpyc: ok
    print("method:", r, v.mm(0.5))
    # An Optional slot binds through the optional type itself.
    print("method:", v.opt(3), v.opt(-3), v.fopt(0.1), v.iopt(3), v.nopt(3))  # tpyc: ok
    print("method:", v.narrow(3), v.narrow(-3), v.mv(7), v.mv(True), v.width(0.25))  # tpyc: ok
    # A constant folded over literals is a literal: it renders as its value.
    r1 = v.mm(1 + 2)  # tpyc: ok
    r2 = v.mm(+3)  # tpyc: ok
    r3 = v.mm(- -3)  # tpyc: ok
    print("method:", r1 + 1, r2 + 1, r3 + 1, v.mv(3 + 4))  # tpyc: ok
    # A float literal reaches float32 through its float value, rounded once.
    print("method:", v.f(1.0000000596046448), v.f(-1.0000000596046448))  # tpyc: ok
    # +-2**63 lies outside every fixed width: still the int variant.
    print("method:", v.mv(9223372036854775808), v.mv(-9223372036854775808))  # tpyc: ok
    # An int and a negated float literal at an Optional float slot.
    print("method:", v.fo(3), v.fo(-0.5))  # tpyc: ok
    # An int literal beyond int64 at an `int` slot.
    print("method:", v.mm(1180591620717411303424))  # tpyc: ok
    calls: list[int32] = []
    # Literal-typed but no constant: evaluated once, then converted.
    r4 = v.mm(3 if ticked(calls) else 3)  # tpyc: ok
    r5 = v.mm((w := 3))  # tpyc: ok
    print("method:", r4 + 1, r5 + 1, w, len(calls))
    # A walrus converts from its own type: to a narrower fixed width, to an
    # Optional float, a float one to float32.
    print("method:", v.narrow((w16 := 3)), v.narrow((w64 := 3000000000)))  # tpyc: ok
    print("method:", v.fo((wf := 3)), v.f((xf := 0.5)))  # tpyc: ok
    # A walrus already holding a BigInt binds the `int` slot as it is.
    print("method:", v.mm((u := 1180591620717411303424)))  # tpyc: ok
    print("method:", w16, w64, wf, xf, u)
    # Constants folded over literals render as their value.
    print("method:", v.f(+0.25), v.f(- -0.5), v.fo(- -0.5), v.mm(~0))  # tpyc: ok
    # An int literal beyond int64 at a float32 slot.
    print("method:", v.f(100000000000000000000))  # tpyc: ok
    # Int64 and uint64 literals at float slots.
    print("method:", v.fo(3000000000), v.f(10000000000000000000))  # tpyc: ok
    # A conditional's literal arms are a `long` in C++; the int32 cast picks.
    print("method:", v.wide(-2147483648 if ticked(calls) else -2147483648), v.wide(-2147483648))  # tpyc: ok


def inverse() -> None:
    d = Dog()
    # Picks the scale leaves as they were: a fixed width the literal fits
    # strictly beats a conversion, and range(5) still counts in int32.
    print("inverse:", p(3), spec(3, d), d.hits, pick(1, 2, 4611686018427387904))  # tpyc: ok
    print("inverse:", amb(1, 2), big(1000000000000), num(3.0))  # tpyc: ok
    total = 0
    for i in range(5):  # tpyc: type(int32)
        total += i
    print("inverse:", total)


def gen() -> Iterator[int]:
    v = V()
    # A generator body renders the pin as a function body does.
    yield v.mm(3)  # tpyc: ok
    yield v.mm(-2)  # tpyc: ok


def generator() -> None:
    print("generator:", [x for x in gen()])


def comprehension() -> None:
    v = V()
    # Inside a comprehension element.
    print("comprehension:", [v.narrow(3) for _ in range(2)], [num(1) for _ in range(2)])  # tpyc: ok


def run(f: Callable[[], str]) -> str:
    return f()


def lambda_body() -> None:
    v = V()
    # Inside a lambda body.
    print("lambda:", run(lambda: v.opt(3) + " " + num(2)))  # tpyc: ok


def main() -> None:
    free_function()
    overload_stubs()
    method()
    inverse()
    generator()
    comprehension()
    lambda_body()


v = V()
# At module level.
print("module:", v.mm(3), num(3), v.narrow(3))  # tpyc: ok
main()
