# Import-cycle member: its non-template member bodies are defined in the .cpp,
# where the peer is complete (the header sees only the peer's forward decls),
# so their signatures may take the peer's classes by value.
from typing import Any, Iterable, Iterator, Protocol, overload
from tpy import Own, Ptr, ReturnException, Span, dispatch, error_return, int32
import cyc_b


class Plain:
    @overload
    def f(self, x: int32) -> int32: ...
    @overload
    def f(self, x: str) -> int32: ...
    def f(self, x: int32 | str) -> int32:
        if isinstance(x, str):
            return cyc_b.twice(len(x))  # tpyc: ok
        return cyc_b.twice(x)


def base() -> int32:
    return 1


def use_plain() -> int32:
    p = Plain()
    return p.f(4) * 100 + p.f("ab")


# a non-template __init__ taking the peer's value type by value
class TakesVal:
    val: int32

    def __init__(self, b: cyc_b.Val) -> None:  # tpyc: ok
        self.val = b.payload


# an @overload method taking the peer's value type by value, called here
# (BUGS.md#cross-module-overload-method-call keeps it from main)
class Merger:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    @overload
    def merge(self, x: cyc_b.Val) -> int32: ...  # tpyc: ok
    @overload
    def merge(self, x: int32) -> int32: ...
    def merge(self, x: cyc_b.Val | int32) -> int32:
        if isinstance(x, cyc_b.Val):
            return self.base + x.payload
        return self.base + x


# a plain class's `-> Iterator[...]` member of a cycle member: its body is in
# the .cpp with the other member bodies, ahead of a later class's loop over it
class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def __iter__(self) -> Iterator[int32]:  # tpyc: ok
        self.items.append(cyc_b.twice(2))
        return iter(self.items)


class BagUser:
    def total(self, b: Bag) -> int32:
        t = 0
        for v in b:
            t += v
        return t


def use_bag() -> str:
    b = Bag()
    t = BagUser().total(b)
    b.items.append(1)
    return f"{t} {len(b.items)}"


class HasGet(Protocol):
    def get(self) -> int32: ...


class Item:
    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n


# an @overload group whose implementation is annotated with a protocol
# return while every stub names a concrete one: only the stubs are emitted
class Maker:
    @overload
    def make(self, x: cyc_b.Val) -> Own[Item]: ...  # tpyc: ok
    @overload
    def make(self, x: int32) -> Own[Item]: ...
    def make(self, x: cyc_b.Val | int32) -> Own[HasGet]:
        if isinstance(x, cyc_b.Val):
            return Item(x.payload)
        return Item(x)


# one @overload group split between a variant kept in the class (its
# `Iterator` return is deduced) and a peer-taking one defined in the .cpp
class Split:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [2, 3]

    @overload
    def __iter__(self, x: int32) -> Iterator[int32]: ...
    @overload
    def __iter__(self, x: cyc_b.Val) -> int32: ...  # tpyc: ok
    def __iter__(self, x: int32 | cyc_b.Val) -> Any:
        if isinstance(x, cyc_b.Val):
            return sum(x.payload + v for v in self.items)
        return iter(self.items)


def use_groups() -> str:
    v = cyc_b.Val(7)
    mk = Maker()
    item = mk.make(v)
    item.n += 1
    sp = Split()
    n = sp.__iter__(v)
    sp.items.append(4)
    for x in sp.__iter__(1):
        n += x
    return f"{item.get()} {n}"


def use_cycle() -> str:
    v = cyc_b.Val(42)
    t = TakesVal(v)
    t.val += 1
    m = Merger(7)
    w = cyc_b.Val(3)
    first = m.merge(w)
    m.base = 10
    return f"{t.val} {first} {m.merge(5)}"


# operators over the peer. A member operator (`operator[]`, `operator()`,
# `operator*`) is defined in the .cpp beside the method it forwards to, so it
# takes or returns the peer's value type by value; a friend operator is
# defined in the class, so it takes a reference-type peer (by reference),
# never a value-type one (error_mutual_operator_method_by_value)
class Adder:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __getitem__(self, x: cyc_b.Val) -> int32:  # tpyc: ok
        return self.k * 100 + x.payload

    def __call__(self, x: cyc_b.Val) -> int32:  # tpyc: ok
        return self.k * 1000 + x.payload

    def __deref__(self) -> cyc_b.Val:  # tpyc: ok
        return cyc_b.Val(-self.k)

    def __floordiv__(self, x: cyc_b.Val) -> int32:  # tpyc: ok
        return x.payload // self.k

    def __sub__(self, x: cyc_b.Item) -> int32:  # tpyc: ok
        return self.k - x.n

    def __eq__(self, x: cyc_b.Item) -> bool:  # tpyc: ok
        return self.k == x.n


def use_operators() -> str:
    a = Adder(2)
    v = cyc_b.Val(7)
    it = cyc_b.Item(5)
    first = a - it
    it.n = 2
    n = a.__deref__()
    a.k = 3
    # `a[v]` is BUGS.md#record-dunder-record-param-rejects
    return (f"{a.__getitem__(v)} {a(v)} {n.payload} {a // v} {first} "
            f"{a - it} {a == it}")


class Refused(Exception, ReturnException):
    pass


# friend operators whose C++ form holds no peer by value: a returned argument
# (the friend forwards a reference), `| None` over one and a `Ptr` (pointers),
# an `@error_return` one (its result slot borrows), a tuple holding a
# reference-type peer (a pointer element) and a `Span` of a value-type peer (a
# view). For `|`, `&`, `^` and `*` the class compiling is the subject: a call
# is BUGS.md#optional-operator-result-use-rejects /
# BUGS.md#error-return-dunder-borrow-bind /
# BUGS.md#tuple-result-operator-call-rejects
class Passer:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __add__(self, x: cyc_b.Item) -> cyc_b.Item:  # tpyc: ok
        return x

    def __or__(self, x: cyc_b.Item) -> cyc_b.Item | None:  # tpyc: ok
        if x.n > self.k:
            return x
        return None

    def __and__(self, x: Ptr[cyc_b.Item]) -> Ptr[cyc_b.Item]:  # tpyc: ok
        return x

    @error_return(Refused)
    def __xor__(self, x: cyc_b.Item) -> cyc_b.Item:  # tpyc: ok
        if x.n < 0:
            raise Refused()
        return x

    def __mul__(self, x: cyc_b.Item) -> tuple[cyc_b.Item, int32]:  # tpyc: ok
        return (x, self.k)

    def __truediv__(self, xs: Span[cyc_b.Val]) -> int32:  # tpyc: ok
        t = self.k
        for v in xs:
            t += v.payload
        return t


def use_passing() -> str:
    p = Passer(2)
    it = cyc_b.Item(1)
    r = p + it
    # the result aliases the argument (an operator's result is readonly)
    it.n = 9
    vals = [cyc_b.Val(3), cyc_b.Val(4)]
    first = p / vals
    vals[0] = cyc_b.Val(10)
    return f"{r.n} {first} {p / vals}"


# a @dispatch group with a template sibling: the non-template variant taking
# the peer by value is defined in the .cpp, the template one in the header
# (calling the set is BUGS.md#dispatch-method-variant-reresolved-by-cpp, the
# `method.overload_set` reject; the class compiling is the subject)
class Disp:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    @dispatch
    def g(self, x: cyc_b.Val) -> int32:  # tpyc: ok
        return x.payload + self.k

    @dispatch
    def g(self, x: Iterable[int32]) -> int32:
        n = self.k
        for v in x:
            n += v
        return n


# overload sets called from a module outside the cycle: C++ overload
# resolution there sees every candidate, the peer-taking ones included
class Opt:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    @dispatch
    def __call__(self, x: int32) -> int32:
        return x * self.k

    @dispatch
    def __call__(self, x: cyc_b.Val | None) -> int32:  # tpyc: ok
        if x is None:
            return 0
        return x.payload

    @dispatch
    def f(self, x: int32) -> int32:
        return x * 10 + self.k

    @dispatch
    def f(self, x: cyc_b.Val | None) -> int32:  # tpyc: ok
        if x is None:
            return -1
        return x.payload


# an @overload implementation annotated with the peer: only the stubs, which
# do not name it, are emitted (header templates over the `Iterable` protocol;
# a call is BUGS.md#dispatch-method-variant-reresolved-by-cpp)
class Box:
    @overload
    def f(self, x: Iterable[int32]) -> int32: ...
    @overload
    def f(self, x: Iterable[int32], y: int32) -> int32: ...
    def f(self, x: Iterable[int32], y: int32 = 0) -> cyc_b.Val | int32:  # tpyc: ok
        t = y
        for v in x:
            t += v
        return t
