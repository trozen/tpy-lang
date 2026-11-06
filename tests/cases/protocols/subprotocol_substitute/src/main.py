# A sub-protocol is usable wherever each protocol it names is: every valid
# redeclaration shape below is accepted and forwarded child -> parent.
from dataclasses import dataclass
from typing import Iterable, Iterator, Protocol, Self

from tpy import Own, ValueType, dynamic, int32, nocopy, readonly


class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Dog(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name)


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


# --- equal redeclaration
class Getter(Protocol):
    def get(self) -> int32: ...


class SameGetter(Getter, Protocol):
    def get(self) -> int32: ...  # tpyc: ok


class Seven:
    def __init__(self) -> None:
        pass

    def get(self) -> int32:
        return 7


def use_getter(g: Getter) -> int32:
    return g.get()


def fwd_same(g: SameGetter) -> int32:
    return use_getter(g)


# --- widened parameter: the child takes any Animal where the parent takes a Dog
class Namer(Protocol):
    def rename(self, x: Dog) -> None: ...


class WideNamer(Namer, Protocol):
    def rename(self, x: Animal) -> None: ...  # tpyc: ok


class Shouter:
    def __init__(self) -> None:
        pass

    def rename(self, x: Animal) -> None:
        x.name = x.name + "!"


def use_namer(n: Namer, d: Dog) -> None:
    n.rename(d)


def fwd_wide(n: WideNamer, d: Dog) -> None:
    use_namer(n, d)


# --- added @readonly: the child promises more than the parent asks
class Peeks(Protocol):
    def width(self) -> int32: ...


class Sees(Peeks, Protocol):
    @readonly
    def width(self) -> int32: ...  # tpyc: ok


class Wide:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w

    @readonly
    def width(self) -> int32:
        return self.w


def use_peeks(p: Peeks) -> int32:
    return p.width()


def fwd_sees(s: Sees) -> int32:
    return use_peeks(s)


def look(w: readonly[Wide]) -> int32:
    return fwd_sees(w)  # a readonly value reaches an all-@readonly sub-protocol


# --- extra parameter with a default
class Scaler(Protocol):
    def f(self, x: int32) -> int32: ...


class OffsetScaler(Scaler, Protocol):
    def f(self, x: int32, y: int32 = 5) -> int32: ...  # tpyc: ok


class Tens:
    def __init__(self) -> None:
        pass

    def f(self, x: int32, y: int32 = 5) -> int32:
        return x * 10 + y


def use_scaler(s: Scaler) -> int32:
    return s.f(1)


def fwd_offset(s: OffsetScaler) -> int32:
    return s.f(2, 7) + use_scaler(s)


# --- Self kept: a `-> Self` requirement is met by `-> Self`
class Chain(Protocol):
    def chain(self) -> Self: ...
    def val(self) -> int32: ...


class SubChain(Chain, Protocol):
    def chain(self) -> Self: ...  # tpyc: ok


class Link:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def chain(self) -> Self:
        self.n += 1
        return self

    def val(self) -> int32:
        return self.n


def use_chain(c: Chain) -> int32:
    return c.chain().val()


def fwd_chain(c: SubChain) -> int32:
    return use_chain(c)


# --- generic parent at its argument, and a two-hop generic chain
class Source[T](Protocol):
    def take(self) -> T: ...


class IntSource(Source[int32], Protocol):
    def take(self) -> int32: ...  # tpyc: ok


class Mid[U](Source[U], Protocol):
    pass


class Leaf[V](Mid[V], Protocol):
    pass


class Nine:
    def __init__(self) -> None:
        pass

    def take(self) -> int32:
        return 9


def use_source(s: Source[int32]) -> int32:
    return s.take()


def fwd_int_source(s: IntSource) -> int32:
    return use_source(s)


def fwd_leaf(s: Leaf[int32]) -> int32:
    return use_source(s)


# --- keyword-only parameters kept by name, an optional one added
class KwScaler(Protocol):
    def g(self, *, x: int32) -> int32: ...


class KwChild(KwScaler, Protocol):
    def g(self, *, x: int32, y: int32 = 1) -> int32: ...  # tpyc: ok


class KwImpl:
    def __init__(self) -> None:
        pass

    def g(self, *, x: int32, y: int32 = 1) -> int32:
        return x * y


def use_kw(k: KwScaler) -> int32:
    return k.g(x=3)


def fwd_kw(k: KwChild) -> int32:
    return k.g(x=2, y=5) + use_kw(k)


# --- an inherited operator dunder on a protocol operand
@dataclass
class Meters(ValueType):
    n: int32

    def __add__(self, other: "Meters") -> "Meters":
        return Meters(self.n + other.n)

    def val(self) -> int32:
        return self.n


class Addable(Protocol):
    def __add__(self, other: Self) -> Own[Self]: ...
    def val(self) -> int32: ...


class SubAddable(Addable, Protocol):
    pass


def add_twice(a: SubAddable) -> None:
    b = a + a  # the parent's `__add__`, through the child
    print("operator", b.val())


# --- generic fields at the child's arguments
class Holder[T](Protocol):
    v: T


class IntHolder(Holder[int32], Protocol):
    v: int32  # tpyc: ok


class GenHolder[U](Holder[U], Protocol):
    v: U  # tpyc: ok


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def use_holder(h: Holder[int32]) -> int32:
    # A protocol field cannot be read through the protocol yet
    # (`Cannot access field`); the forward is the subject here.
    return 1


def fwd_holders(a: IntHolder, b: GenHolder[int32]) -> int32:
    return use_holder(a) + use_holder(b)


# --- a @dynamic sub-protocol forwarded at its argument
@dynamic
class DynSource[T](Protocol):
    def take(self) -> T: ...


@dynamic
class DynChild[T](DynSource[T], Protocol):
    pass


class DynNine(DynChild[int32]):
    def __init__(self) -> None:
        pass

    def take(self) -> int32:
        return 9


def use_dyn(s: DynSource[int32]) -> int32:
    return s.take()


def fwd_dyn(c: DynChild[int32]) -> int32:
    return use_dyn(c)


# --- diamond: the member is Right's (Python's MRO), which meets both sides
class Root(Protocol):
    def f(self) -> int32: ...


class Left(Root, Protocol):
    pass


class Right(Root, Protocol):
    @readonly
    def f(self) -> int32: ...


class Diamond(Left, Right, Protocol):  # tpyc: ok
    pass


class Fixed:
    def __init__(self) -> None:
        pass

    @readonly
    def f(self) -> int32:
        return 4


def use_right(r: Right) -> int32:
    return r.f()


def fwd_diamond(d: Diamond) -> int32:
    return use_right(d)


# --- a sub-protocol of Iterator[int32] at an Iterable[int32] parameter
class SubIter(Iterator[int32], Protocol):
    pass


class Count:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> int32:
        if self.i >= 3:
            raise StopIteration
        self.i += 1
        return self.i


def use_iterable(xs: Iterable[int32]) -> int32:
    total = 0
    for x in xs:
        total += x
    return total


def fwd_sub_iter(it: SubIter) -> int32:
    total = 0
    for x in it:  # loops over the sub-protocol directly
        total += x
        break
    return total + use_iterable(it)


class GenSubIter[T](Iterator[T], Protocol):
    pass


def sum_gen_sub_iter(it: GenSubIter[int32]) -> int32:
    total = 0
    for x in it:
        total += x
    return total


# --- a lending sub-protocol: its loop aliases the live cells; forwarded to
# an owning parameter it copies the elements, with the warning a lending
# class gets there (the consumer only reads, so the copy is unobservable)
class Lent(Iterator[Cell], Protocol):
    def __next__(self) -> Cell: ...  # tpyc: ok


class Cells:
    i: int32
    cells: list[Cell]

    def __init__(self) -> None:
        self.i = 0
        self.cells = [Cell(1), Cell(2)]

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Cell:
        if self.i >= len(self.cells):
            raise StopIteration
        self.i += 1
        return self.cells[self.i - 1]


def bump_lent(xs: Lent) -> None:
    for c in xs:
        c.v += 10  # writes through to the live cells


def sum_owned(xs: Iterator[Own[Cell]]) -> int32:
    total = 0
    for c in xs:
        total += c.v
    return total


def fwd_lent_owned(xs: Lent) -> int32:
    return sum_owned(xs)  # tpyc: warning(/copies Cell elements/)


# --- an Iterable sub-protocol whose __iter__ returns the lending one
class Bag(Iterable[Cell], Protocol):
    def __iter__(self) -> Lent: ...  # tpyc: ok


class CellBag:
    i: int32
    cells: list[Cell]

    def __init__(self) -> None:
        self.i = 0
        self.cells = [Cell(5), Cell(6)]

    def __iter__(self) -> Self:  # a Lent: the loop walks the caller's cells
        return self

    def __next__(self) -> Cell:
        if self.i >= len(self.cells):
            raise StopIteration
        self.i += 1
        return self.cells[self.i - 1]


def bump_bag(bag: Bag) -> None:
    for c in bag:  # the element is Lent's: a Cell, aliased
        c.v += 10


# --- a __next__ whose extra parameter has a default still yields the element
class Fallback(Iterator[int32], Protocol):
    def __next__(self, fallback: int32 = 0) -> int: ...  # tpyc: ok


class Big:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self, fallback: int32 = 0) -> int:
        if self.i > 0:
            raise StopIteration
        self.i += 1
        return 1000000000000


def first_big(xs: Fallback) -> int:
    total: int = 0
    for x in xs:  # the element is the effective __next__'s `int`
        total += x
    return total


# --- call shapes a class may differ in: a renamed positional parameter,
# a positional-only one, an added optional keyword-only one
class Named(Protocol):
    def f(self, x: int32) -> int32: ...


class PosOnly(Protocol):
    def h(self, x: int32, /) -> int32: ...


class Renamed:
    def __init__(self) -> None:
        pass

    def f(self, y: int32) -> int32:  # tpyc: ok
        return y + 1


class PosOnlyMember:
    def __init__(self) -> None:
        pass

    def f(self, x: int32, /) -> int32:  # tpyc: ok
        return x + 10


class RenamedPosOnly:
    def __init__(self) -> None:
        pass

    def h(self, z: int32) -> int32:  # tpyc: ok
        return z + 100


class ExtraOptKw:
    def __init__(self) -> None:
        pass

    def f(self, x: int32, *, scale: int32 = 3) -> int32:  # tpyc: ok
        return x * scale


def use_named(p: Named) -> int32:
    return p.f(1)


def use_posonly(p: PosOnly) -> int32:
    return p.h(2)


# --- an owning sub-protocol of @nocopy elements: each element is moved out
@nocopy
class Token:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Owning(Iterator[Own[Token]], Protocol):
    def __next__(self) -> Own[Token]: ...  # tpyc: ok


class Tokens:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[Token]:
        if self.i >= 3:
            raise StopIteration
        self.i += 1
        return Token(self.i)


def keep_tokens(xs: Owning) -> Own[list[Token]]:
    out: list[Token] = []
    for t in xs:
        out.append(t)  # moved, never copied
    return out


def main() -> None:
    print("same", fwd_same(Seven()))
    d = Dog("rex")
    fwd_wide(Shouter(), d)
    print("wide", d.name)
    print("sees", fwd_sees(Wide(6)), look(Wide(8)))
    print("offset", fwd_offset(Tens()))
    link = Link(1)
    print("self", fwd_chain(link), link.n)
    print("generic", fwd_int_source(Nine()), fwd_leaf(Nine()))
    print("keyword", fwd_kw(KwImpl()))
    add_twice(Meters(2))
    print("fields", fwd_holders(Box(1), Box(2)))
    print("dynamic", fwd_dyn(DynNine()))
    print("diamond", fwd_diamond(Fixed()))
    print("subiter", fwd_sub_iter(Count()), sum_gen_sub_iter(Count()))
    src = Cells()
    bump_lent(src)
    print("lent", src.cells[0].v, src.cells[1].v)
    print("lent owned", fwd_lent_owned(Cells()))
    bag = CellBag()
    bump_bag(bag)
    print("bag", bag.cells[0].v, bag.cells[1].v)
    print("fallback", first_big(Big()))
    print("shapes renamed", use_named(Renamed()))
    print("shapes posonly", use_named(PosOnlyMember()))
    print("shapes renamed posonly", use_posonly(RenamedPosOnly()))
    print("shapes extra optional keyword", use_named(ExtraOptKw()))
    toks = keep_tokens(Tokens())
    print("owning", len(toks), toks[2].v)


main()
