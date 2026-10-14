# Overload ranking by declared shape, winner declared first (the other order:
# calls/dispatch_rank_declared_shape_order); record variants bump `hits`.
from typing import Callable, Iterable, Literal, Protocol, overload
from tpy import dispatch, readonly, AnyFixedInt, Own, Ptr, float32, int8, int32, int64


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


# The coerce_* sections: an int8 / int / float literal converts at every
# variant, so the conversion stage ranks them -- by the same cells as the
# strict stage.
@dispatch
def cbp(x: Animal, n: int64) -> str:
    x.hits += 1
    return "base"


@dispatch
def cbp(x: Named, n: int64) -> str:
    x.bump(10)
    return "proto"


def cbp_via(d: Dog, n: int8) -> str:
    return cbp(d, n)  # tpyc: ok


@dispatch
def cbf(x: Animal, y: float32) -> str:
    x.hits += 1
    return "base"


@dispatch
def cbf(x: Named, y: float32) -> str:
    x.bump(10)
    return "proto"


@dispatch
def cpp(x: Animal, p: Ptr[Animal]) -> str:
    p.hits += 1
    return "base"


@dispatch
def cpp(x: Named, p: Ptr[Animal]) -> str:
    p.hits += 10
    return "proto"


def cb_via(d: Dog, n: int8) -> str:
    return cb(d, n)  # tpyc: ok


@dispatch
def cew(xs: Iterable[int32], n: int64) -> str:
    return "i32"


@dispatch
def cew(xs: Iterable[int64], n: int64) -> str:
    return "i64"


@dispatch
def ceg[T: AnyFixedInt](xs: Iterable[T], n: int64) -> str:
    return "generic"


@dispatch
def ceg(xs: Iterable[int32], n: int64) -> str:
    return "i32"


@dispatch
def cgt[T](x: Iterable[T], n: int64) -> str:
    return "genproto"


@dispatch
def cgt[T](x: T, n: int64) -> str:
    return "bare"


@dispatch
def cls(x: Literal["r", "w"], n: int64) -> str:
    return "lit"


@dispatch
def cls(x: str, n: int64) -> str:
    return "str"


@dispatch
def clf(x: Literal["r", "w"], y: float32) -> str:
    return "lit"


@dispatch
def clf(x: str, y: float32) -> str:
    return "str"


@dispatch
def cob(x: Animal | None, n: int64) -> str:
    if x is not None:
        x.hits += 1
    return "opt"


@dispatch
def cob[T](x: T, n: int64) -> str:
    return "bare"


@dispatch
def cwi(x: int64) -> str:
    return "int64"


@dispatch
def cwi(x: int) -> str:
    return "int"


@dispatch
def cpt(x: Named, n: int64) -> str:
    x.bump(1)
    return "proto"


@dispatch
def cpt[T](x: T, n: int64) -> str:
    return "bare"


def cpt_via(d: Dog, n: int8) -> str:
    return cpt(d, n)  # tpyc: ok


@dispatch
def csg[T](x: list[T], n: int64) -> str:
    x.clear()
    return "shape"


@dispatch
def csg[T](x: Iterable[T], n: int64) -> str:
    return "genproto"


@dispatch
def cst[T](x: list[T], n: int64) -> str:
    x.clear()
    return "shape"


@dispatch
def cst[T](x: T, n: int64) -> str:
    return "bare"


@dispatch
def csf[T](x: list[T], y: float32) -> str:
    x.clear()
    return "shape"


@dispatch
def csf[T](x: T, y: float32) -> str:
    return "bare"


@dispatch
def csp(x: Dog, n: int64) -> str:
    x.hits += 1
    return "dog"


@dispatch
def csp(x: Named, n: int64) -> str:
    x.bump(10)
    return "proto"


class Walker:
    def __init__(self) -> None:
        pass

    @dispatch
    def go(self, x: int64) -> str:
        return "int64"

    @dispatch
    def go(self, x: int) -> str:
        return "int"


@dispatch
def cgo(x: Dog, n: int64 | None) -> str:
    x.hits += 1
    return "dog"


@dispatch
def cgo(x: Animal, n: int64) -> str:
    x.hits += 10
    return "animal"


@dispatch
def cgl(x: Dog, n: int) -> str:
    x.hits += 1
    return "dog"


@dispatch
def cgl(x: Animal, n: int | None) -> str:
    x.hits += 10
    return "animal"


@dispatch
def ctie(x: Animal, y: Dog, z: int64) -> str:
    y.hits += 1
    return "animal_dog"


@dispatch
def ctie(x: Dog, y: Animal, z: int64) -> str:
    y.hits += 10
    return "dog_animal"


@dispatch
def ccw(xs: list[int64], n: int64, trigger: int64) -> str:
    return "i64"


@dispatch
def ccw(xs: list[int32], n: float, trigger: int64) -> str:
    return "i32-float"


@dispatch
def cfn(g: Callable[[int32], int32], x: Animal, n: int64) -> str:
    x.hits += g(1)
    return "base"


@dispatch
def cfn(g: Callable[[int32], int32], x: Named, n: int64) -> str:
    x.bump(g(10))
    return "proto"


@dispatch
def cfb(g: Callable[[int32], int32], x: Animal, n: int64) -> str:
    x.hits += g(1)
    return "base"


@dispatch
def cfb[T](g: Callable[[int32], int32], x: T, n: int64) -> str:
    return "bare"


@dispatch
def cli(x: Animal, y: Animal, v: int64, n: int64) -> str:
    y.hits += 1
    return "bases-i64"


@dispatch
def cli(x: Dog, y: Dog, v: int, n: int64) -> str:
    y.hits += 10
    return "dogs-int"


@dispatch
def clr(x: float, n: int64) -> str:
    return "f64"


@dispatch
def clr(x: float32, n: int64) -> str:
    return "f32"


def coerce_base_vs_proto() -> None:
    d = Dog()
    n8: int8 = 1
    k: int = 1
    # A base-class parameter beats a protocol when the int8 / int converts,
    # from a local and from a parameter.
    print("coerce_base_vs_proto:", cbp(d, n8), cbp(d, k), cbp_via(d, n8), d.hits)  # tpyc: ok
    # A float literal converting at float32 ranks the same way.
    print("coerce_base_vs_proto:", cbf(d, 1.5), d.hits)  # tpyc: ok
    # Keyword arguments rank the same way.
    print("coerce_base_vs_proto:", cbp(n=n8, x=d), d.hits)  # tpyc: ok


def coerce_ptr() -> None:
    d = Dog()
    other = Dog()
    # A Dog value converting at Ptr[Animal]: the base still beats the
    # protocol, and the variant writes through the pointer.
    print("coerce_ptr:", cpp(d, other), other.hits)  # tpyc: ok


def coerce_base_vs_t_param() -> None:
    d = Dog()
    n: int8 = 3
    # A Dog parameter ranks as a Dog local does: the base beats bare T.
    print("coerce_base_vs_t_param:", cb_via(d, n), d.hits)  # tpyc: ok


def coerce_elem_widen() -> None:
    xs: list[int32] = [1, 2]
    x8: list[int8] = [1, 2]
    n8: int8 = 1
    # The view that widens no element wins.
    print("coerce_elem_widen:", cew(xs, n8), ceg(x8, n8))  # tpyc: ok


def coerce_generic_shapes() -> None:
    xs: list[int32] = [7, 8]
    ys: list[int32] = [7, 8]
    zs: list[int32] = [7, 8]
    n8: int8 = 1
    # A generic protocol beats bare T; a generic shape beats both.
    print("coerce_generic_shapes:", cgt(xs, n8))  # tpyc: ok
    print("coerce_generic_shapes:", csg(ys, n8), len(ys))  # tpyc: ok
    print("coerce_generic_shapes:", cst(zs, n8), len(zs))  # tpyc: ok
    print("coerce_generic_shapes:", csf(xs, 1.5), len(xs))  # tpyc: ok


def coerce_proto_vs_t() -> None:
    d = Dog()
    n8: int8 = 1
    # A protocol beats bare T, from a local and from a parameter.
    print("coerce_proto_vs_t:", cpt(d, n8), cpt_via(d, n8), d.hits)  # tpyc: ok


def coerce_literal_vs_str() -> None:
    n8: int8 = 1
    # A Literal parameter holding the value beats str.
    print("coerce_literal_vs_str:", cls("r", n8), clf("r", 1.5))  # tpyc: ok


def coerce_optional_base() -> None:
    d = Dog()
    n8: int8 = 1
    # A base inside an Optional beats bare T and writes through the parameter.
    print("coerce_optional_base:", cob(d, n8), d.hits)  # tpyc: ok


def coerce_width() -> None:
    n8: int8 = 1
    # int8 widens less to int64 than to int.
    print("coerce_width:", cwi(n8))  # tpyc: ok


def coerce_subclass_vs_proto() -> None:
    p = Puppy()
    n8: int8 = 1
    # A subclass at its base beats a protocol.
    print("coerce_subclass_vs_proto:", csp(p, n8), p.hits)  # tpyc: ok


def coerce_method() -> None:
    w = Walker()
    n8: int8 = 1
    # A method set ranks the same: int8 widens less to int64 than to int.
    print("coerce_method:", w.go(n8))  # tpyc: ok


def coerce_optional_scalar() -> None:
    p = Puppy()
    n8: int8 = 1
    # The int8 converts into the Optional payload as into a plain int64,
    # so the nearer base decides.
    print("coerce_optional_scalar:", cgo(p, n8), p.hits)  # tpyc: ok
    q = Puppy()
    # The int literal is as far from the default width at `int | None` as
    # at `int`, so the nearer base decides.
    print("coerce_optional_scalar:", cgl(q, 5), q.hits)  # tpyc: ok


def coerce_tie_escape() -> None:
    d = Dog()
    # The DECLARED type picks the variant; the warning is that spelling's cost.
    a: Animal = d  # tpyc: warning(/upcast narrows/)
    n8: int8 = 1
    # Two Dogs at the crossed pair tie (Ambiguous overload); typing the first
    # one as Animal picks one variant.
    print("coerce_tie_escape:", ctie(a, d, n8), d.hits)  # tpyc: ok


def coerce_container() -> None:
    n64: int64 = 1
    n8: int8 = 1
    # Fewer conversions beat a narrower list: the list[int64] variant
    # converts only the int8, so it wins as in its strict twin.
    print("coerce_container:", ccw([1, 2], n64, n8), ccw([1, 2], n64, n64))  # tpyc: ok


def coerce_callable() -> None:
    d = Dog()
    n8: int8 = 1
    n64: int64 = 1
    # A callable argument beside a converting int8: the base beats the protocol.
    print("coerce_callable:", cfn(lambda v: v, d, n8), cfn(lambda v: v, d, n64), d.hits)  # tpyc: ok


def coerce_callable_base() -> None:
    d = Dog()
    n8: int8 = 1
    # A callable argument beside a converting int8: the base beats bare T.
    print("coerce_callable_base:", cfb(lambda v: v, d, n8), d.hits)  # tpyc: ok


def coerce_literal_int() -> None:
    d = Dog()
    n8: int8 = 1
    n64: int64 = 1
    # The int literal converts at `int` as at int64, so two bases at exact
    # int64 beat two subclasses at `int`.
    print("coerce_literal_int:", cli(d, d, 5, n8), cli(d, d, 5, n64), d.hits)  # tpyc: ok


def coerce_literal_range() -> None:
    n8: int8 = 1
    # A float literal float32 cannot hold is no conversion there.
    print("coerce_literal_range:", clr(1e300, n8))  # tpyc: ok


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
    coerce_base_vs_proto()
    coerce_ptr()
    coerce_base_vs_t_param()
    coerce_elem_widen()
    coerce_generic_shapes()
    coerce_proto_vs_t()
    coerce_literal_vs_str()
    coerce_optional_base()
    coerce_width()
    coerce_subclass_vs_proto()
    coerce_method()
    coerce_optional_scalar()
    coerce_tie_escape()
    coerce_container()
    coerce_callable()
    coerce_callable_base()
    coerce_literal_int()
    coerce_literal_range()


main()
