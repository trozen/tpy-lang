# Overload ranking by declared shape, winner declared first (the other order:
# calls/dispatch_rank_declared_shape_order); record variants bump `hits`.
from typing import Callable, Protocol, overload
from tpy import dispatch, readonly, Own, int8, int32, int64


class Named(Protocol):
    def name(self) -> str: ...

    def bump(self, n: int32) -> None: ...


class Animal:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @readonly
    def name(self) -> str:
        return "animal"

    def bump(self, n: int32) -> None:
        self.hits += n


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


class Puppy(Dog):
    def __init__(self) -> None:
        super().__init__()


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@dispatch
def gp(x: Named) -> str:
    x.bump(1)
    return "proto"


@dispatch
def gp[T](x: T) -> str:
    return "bare"


@dispatch
def gb(x: Animal) -> str:
    x.hits += 1
    return "base"


@dispatch
def gb[T](x: T) -> str:
    return "bare"


@dispatch
def bp(x: Animal) -> str:
    x.hits += 10
    return "base"


@dispatch
def bp(x: Named) -> str:
    x.bump(1)
    return "proto"


@dispatch
def nb(x: Dog) -> str:
    x.hits += 10
    return "dog"


@dispatch
def nb(x: Animal) -> str:
    x.hits += 1
    return "animal"


@dispatch
def sp(x: Dog) -> str:
    x.hits += 10
    return "dog"


@dispatch
def sp(x: Named) -> str:
    x.bump(1)
    return "proto"


@dispatch
def sh[T](x: list[T]) -> str:
    x.clear()
    return "shape"


@dispatch
def sh[T](x: T) -> str:
    return "bare"


@dispatch
def ec(x: int32) -> str:
    return "concrete"


@dispatch
def ec[T](x: T) -> str:
    return "bare"


@dispatch
def mx(x: list[int32], y: int32) -> str:
    x.append(y)
    return "concrete"


@dispatch
def mx[T](x: list[T], y: T) -> str:
    return "shape+bare"


@dispatch
def gr(x: Named) -> str:
    x.bump(1)
    return "proto"


@dispatch
def gr[T](x: readonly[T]) -> str:
    return "bare"


class Keeper:
    def __init__(self) -> None:
        pass

    @dispatch
    def f(self, x: Dog) -> str:
        x.hits += 10
        return "dog"

    @dispatch
    def f(self, x: Animal) -> str:
        x.hits += 1
        return "animal"


class MA:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class MB:
    marks: int32

    def __init__(self) -> None:
        self.marks = 0


class MC(MA, MB):
    def __init__(self) -> None:
        MA.__init__(self)
        MB.__init__(self)


@dispatch
def mb(x: MA) -> str:
    x.hits += 1
    return "a"


@dispatch
def mb(x: MB) -> str:
    x.marks += 10
    return "b"


class A2:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class B2(A2):
    def __init__(self) -> None:
        super().__init__()


class C2:
    marks: int32

    def __init__(self) -> None:
        self.marks = 0


class D2(B2, C2):
    def __init__(self) -> None:
        B2.__init__(self)
        C2.__init__(self)


@dispatch
def dm(x: A2) -> str:
    x.hits += 1
    return "a2"


@dispatch
def dm(x: C2) -> str:
    x.marks += 10
    return "c2"


@overload
def h(a: Animal) -> float: ...
@overload
def h(a: Dog) -> int: ...
def h(a: Animal | Dog) -> float | int:
    if isinstance(a, Dog):
        a.hits += 10
        return 1
    a.hits += 1
    return 2.5


@dispatch
def cn(a: Dog, x: float) -> str:
    a.hits += 10
    return "dog"


@dispatch
def cn(a: Animal, x: float) -> str:
    a.hits += 1
    return "animal"


@dispatch
def ob(x: Animal | None) -> str:
    if x is not None:
        x.hits += 1
    return "opt"


@dispatch
def ob[T](x: T) -> str:
    return "bare"


@dispatch
# The warning is right: the variant only bumps `x`, it never keeps it.
def ow(x: Own[Animal]) -> str:  # tpyc: warning(/never consumed/)
    x.hits += 1
    return "own"


@dispatch
def ow[T](x: T) -> str:
    return "bare"


@dispatch
def cb(a: Animal, n: int64) -> str:
    a.hits += 1
    return "base"


@dispatch
def cb[T](a: T, n: int64) -> str:
    return "bare"


class Twins:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    @readonly(False)
    def f(self, x: bool) -> str:
        self.hits += 1
        return "mut"

    @dispatch
    @readonly
    def f(self, x: bool) -> str:
        return "ro"

    # The same twins with @readonly outside @dispatch.
    @readonly(False)
    @dispatch
    def g(self, x: bool) -> str:
        self.hits += 10
        return "mut"

    @readonly
    @dispatch
    def g(self, y: "bool") -> str:
        return "ro"


@dispatch
def pi[T](x: T) -> str:
    return "bare"


@dispatch
def pi(x: int) -> str:
    return "int"


@dispatch
def apply(xs: list[Rec], f: Callable[[Rec], int32]) -> int32:
    xs[0].n += f(xs[0])
    return 100 + len(xs)


@dispatch
def apply[T](xs: list[Rec], f: Callable[[Rec], T]) -> int32:
    return len(xs)


def proto_vs_t() -> None:
    d = Dog()
    # Named beats bare T; the protocol variant bumps the caller's Dog.
    print("proto_vs_t:", gp(d), d.hits)  # tpyc: ok


def base_vs_t() -> None:
    d = Dog()
    # A base-class parameter beats bare T.
    print("base_vs_t:", gb(d), d.hits)  # tpyc: ok


def base_vs_proto() -> None:
    d = Dog()
    # A base-class parameter beats a protocol.
    print("base_vs_proto:", bp(d), d.hits)  # tpyc: ok


def nearest_base() -> None:
    p = Puppy()
    d = Dog()
    # The nearer base (Dog) wins and bumps the caller's object.
    print("nearest_base:", nb(p), p.hits)  # tpyc: ok
    print("nearest_base:", nb(d), d.hits)  # tpyc: ok


def subclass_vs_proto() -> None:
    p = Puppy()
    # A subclass at a base-class parameter beats a protocol.
    print("subclass_vs_proto:", sp(p), p.hits)  # tpyc: ok


def shape_vs_t() -> None:
    xs: list[int32] = [7, 8]
    # A generic over a concrete shape beats bare T and clears the caller's list.
    print("shape_vs_t:", sh(xs), len(xs))  # tpyc: ok


def concrete_vs_t() -> None:
    y: int32 = 3
    # An exact concrete parameter beats bare T.
    print("concrete_vs_t:", ec(y))  # tpyc: ok


def mixed() -> None:
    xs: list[int32] = [7, 8]
    y: int32 = 3
    # Exact on both arguments beats shape + bare T.
    print("mixed:", mx(xs, y), len(xs))  # tpyc: ok


def t_only() -> None:
    y: int32 = 3
    # Only bare T fits an int, so the fallback runs.
    print("t_only:", gp(y))  # tpyc: ok


def readonly_t() -> None:
    d = Dog()
    # readonly[T] ranks as bare T, below a protocol.
    print("readonly_t:", gr(d), d.hits)  # tpyc: ok


def method() -> None:
    k = Keeper()
    p = Puppy()
    # A method set ranks the same: the nearer base wins.
    print("method:", k.f(p), p.hits)  # tpyc: ok


def typing_overload() -> None:
    p = Puppy()
    # The Dog stub (int result) wins over the Animal stub (float result).
    print("typing_overload:", h(p), p.hits)  # tpyc: ok


def sum_narrow() -> None:
    xs: list[int8] = [10, 20]
    # The generic sum keeps int8: it converts nothing, the int32 variant would.
    r = sum(xs)  # tpyc: type(int8)
    print("sum_narrow:", r)
    s8: int8 = sum(xs)  # tpyc: ok
    print("sum_narrow:", s8)
    a8: int8 = 5
    b8: int8 = 6
    rl = sum([a8, b8])  # tpyc: type(int8)
    print("sum_narrow:", rl)
    d: dict[int8, int32] = {1: 2, 3: 4}
    # A dict sums its keys; its values view sums the values.
    rd = sum(d)  # tpyc: type(int8)
    rk = sum(d.keys())  # tpyc: type(int8)
    rv = sum(d.values())  # tpyc: type(int32)
    print("sum_narrow:", rd, rk, rv)


def multi_base() -> None:
    c = MC()
    # MA is first in MC's MRO, so mb(MA) beats mb(MB).
    print("multi_base:", mb(c), c.hits, c.marks)  # tpyc: ok
    d = D2()
    # D2's MRO is D2, B2, A2, C2: the grandparent A2 is nearer than the
    # direct base C2, so dm(A2) wins.
    print("multi_base:", dm(d), d.hits, d.marks)  # tpyc: ok


def coerce_nearest() -> None:
    p = Puppy()
    n: int32 = 2
    y = 1.5
    # The float argument converts at both variants; the nearer base still wins.
    print("coerce_nearest:", cn(p, 1.5), cn(p, n), cn(p, y), p.hits)  # tpyc: ok


def optional_base() -> None:
    d = Dog()
    # A base class inside an Optional beats bare T.
    print("optional_base:", ob(d), d.hits)  # tpyc: ok


def coerce_base_vs_t() -> None:
    d = Dog()
    n: int8 = 3
    # The int8 converts at both variants; the concrete base still beats bare T.
    print("coerce_base_vs_t:", cb(d, n), d.hits)  # tpyc: ok


def receiver_twins() -> None:
    t = Twins()
    # Twins differing only in receiver qualification: a mutable receiver
    # takes the mutating one.
    print("receiver_twins:", t.f(True), t.g(True), t.hits)  # tpyc: ok


# A quoted name under Own[...] ranks as the class it names.
@dispatch
def oq(x: Own["Animal"]) -> str:  # tpyc: warning(/never consumed/)
    return "own"


@dispatch
def oq[T](x: T) -> str:
    return "bare"


def peek(a: readonly["Animal"] | None) -> bool:
    # A quoted name under readonly[...] | None must also load under CPython.
    return a is not None


def own_base() -> None:
    # An exact record at an owned slot beats bare T (the stub reads Own[X] as X).
    a = Animal()
    print("own_base:", ow(Animal()), oq(Animal()), peek(a))  # tpyc: ok


def plain_int() -> None:
    y: int32 = 3
    # An int32 at bare T converts nothing; at `int` it would widen.
    print("plain_int:", pi(1), pi(y))  # tpyc: ok


def callable_result() -> None:
    rs = [Rec(2), Rec(1)]
    # A concrete callable result beats a generic one.
    print("callable_result:", apply(rs, lambda r: r.n), rs[0].n)  # tpyc: ok


def main() -> None:
    proto_vs_t()
    base_vs_t()
    base_vs_proto()
    nearest_base()
    subclass_vs_proto()
    shape_vs_t()
    concrete_vs_t()
    mixed()
    t_only()
    readonly_t()
    method()
    typing_overload()
    sum_narrow()
    multi_base()
    coerce_nearest()
    optional_base()
    coerce_base_vs_t()
    receiver_twins()
    own_base()
    plain_int()
    callable_result()


main()
