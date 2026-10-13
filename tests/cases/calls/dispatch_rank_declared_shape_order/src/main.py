# Overload ranking ignores declaration order: every section declares the winner
# after a weaker one (winner-first twin: calls/dispatch_rank_declared_shape).
from typing import Callable, Protocol
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
def gp[T](x: T) -> str:
    return "bare"


@dispatch
def gp(x: Named) -> str:
    x.bump(1)
    return "proto"


@dispatch
def gb[T](x: T) -> str:
    return "bare"


@dispatch
def gb(x: Animal) -> str:
    x.hits += 1
    return "base"


@dispatch
def bp(x: Named) -> str:
    x.bump(1)
    return "proto"


@dispatch
def bp(x: Animal) -> str:
    x.hits += 10
    return "base"


@dispatch
def nb(x: Animal) -> str:
    x.hits += 1
    return "animal"


@dispatch
def nb(x: Dog) -> str:
    x.hits += 10
    return "dog"


@dispatch
def sp(x: Named) -> str:
    x.bump(1)
    return "proto"


@dispatch
def sp(x: Dog) -> str:
    x.hits += 10
    return "dog"


@dispatch
def sh[T](x: T) -> str:
    return "bare"


@dispatch
def sh[T](x: list[T]) -> str:
    x.clear()
    return "shape"


@dispatch
def ec[T](x: T) -> str:
    return "bare"


@dispatch
def ec(x: int32) -> str:
    return "concrete"


@dispatch
def mx[T](x: list[T], y: T) -> str:
    return "shape+bare"


@dispatch
def mx(x: list[int32], y: int32) -> str:
    x.append(y)
    return "concrete"


@dispatch
def gr[T](x: readonly[T]) -> str:
    return "bare"


@dispatch
def gr(x: Named) -> str:
    x.bump(1)
    return "proto"


class Keeper:
    def __init__(self) -> None:
        pass

    @dispatch
    def f(self, x: Animal) -> str:
        x.hits += 1
        return "animal"

    @dispatch
    def f(self, x: Dog) -> str:
        x.hits += 10
        return "dog"


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
def mb(x: MB) -> str:
    x.marks += 10
    return "b"


@dispatch
def mb(x: MA) -> str:
    x.hits += 1
    return "a"


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
def dm(x: C2) -> str:
    x.marks += 10
    return "c2"


@dispatch
def dm(x: A2) -> str:
    x.hits += 1
    return "a2"


@dispatch
def cn(a: Animal, x: float) -> str:
    a.hits += 1
    return "animal"


@dispatch
def cn(a: Dog, x: float) -> str:
    a.hits += 10
    return "dog"


@dispatch
def ob[T](x: T) -> str:
    return "bare"


@dispatch
def ob(x: Animal | None) -> str:
    if x is not None:
        x.hits += 1
    return "opt"


@dispatch
def ow[T](x: T) -> str:
    return "bare"


@dispatch
# The warning is right: the variant only bumps `x`, it never keeps it.
def ow(x: Own[Animal]) -> str:  # tpyc: warning(/never consumed/)
    x.hits += 1
    return "own"


@dispatch
def cb[T](a: T, n: int64) -> str:
    return "bare"


@dispatch
def cb(a: Animal, n: int64) -> str:
    a.hits += 1
    return "base"


class Twins:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    # Receiver twins, the readonly one declared first.
    @readonly
    @dispatch
    def g(self, y: "bool") -> str:
        return "ro"

    @readonly(False)
    @dispatch
    def g(self, x: bool) -> str:
        self.hits += 10
        return "mut"


@dispatch
def pi(x: int) -> str:
    return "int"


@dispatch
def pi[T](x: T) -> str:
    return "bare"


@dispatch
def apply[T](xs: list[Rec], f: Callable[[Rec], T]) -> int32:
    return len(xs)


@dispatch
def apply(xs: list[Rec], f: Callable[[Rec], int32]) -> int32:
    xs[0].n += f(xs[0])
    return 100 + len(xs)


def proto_vs_t() -> None:
    d = Dog()
    # Named beats bare T although gp[T] is declared first.
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
    # The nearer base (Dog) wins although nb(Animal) is declared first.
    print("nearest_base:", nb(p), p.hits)  # tpyc: ok
    print("nearest_base:", nb(d), d.hits)  # tpyc: ok


def subclass_vs_proto() -> None:
    p = Puppy()
    # A subclass at a base-class parameter beats a protocol.
    print("subclass_vs_proto:", sp(p), p.hits)  # tpyc: ok


def shape_vs_t() -> None:
    xs: list[int32] = [7, 8]
    # A generic over a concrete shape beats bare T.
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


def readonly_t() -> None:
    d = Dog()
    # readonly[T] ranks as bare T, below a protocol.
    print("readonly_t:", gr(d), d.hits)  # tpyc: ok


def method() -> None:
    k = Keeper()
    p = Puppy()
    # A method set ranks the same: the nearer base wins.
    print("method:", k.f(p), p.hits)  # tpyc: ok


def multi_base() -> None:
    c = MC()
    # MA is first in MC's MRO, so mb(MA) wins although mb(MB) is declared first.
    print("multi_base:", mb(c), c.hits, c.marks)  # tpyc: ok
    d = D2()
    # D2's MRO is D2, B2, A2, C2: the grandparent A2 is nearer than the
    # direct base C2, so dm(A2) wins although dm(C2) is declared first.
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
    # A mutable receiver takes the mutating twin.
    print("receiver_twins:", t.g(True), t.hits)  # tpyc: ok


@dispatch
def oq[T](x: T) -> str:
    return "bare"


# A quoted name under Own[...] ranks as the class it names.
@dispatch
def oq(x: Own["Animal"]) -> str:  # tpyc: warning(/never consumed/)
    return "own"


def peek(a: readonly["Animal"] | None) -> bool:
    return a is not None


def own_base() -> None:
    # An exact record at an owned slot beats bare T.
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
    readonly_t()
    method()
    multi_base()
    coerce_nearest()
    optional_base()
    coerce_base_vs_t()
    receiver_twins()
    own_base()
    plain_int()
    callable_result()


main()
