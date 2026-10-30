# A record argument at a structural protocol parameter binds the caller's object
# at every callee position (`Rect` is @nocopy, so a silent copy would not build).
import asyncio
from typing import Iterator, Protocol, Sized, overload

from tpy import AnyFixedInt, Spannable, ValueType, int32, nocopy, readonly


class HasArea(Protocol):
    def area(self) -> int32: ...


class Growable(Protocol):
    def grow(self) -> None: ...


@nocopy
class Rect:
    w: int32
    h: int32

    def __init__(self, w: int32, h: int32) -> None:
        self.w = w
        self.h = h

    def area(self) -> int32:
        return self.w * self.h

    def grow(self) -> None:
        self.w += 1


class Inner:
    rect: Rect

    def __init__(self) -> None:
        self.rect = Rect(2, 3)


class Holder:
    rect: Rect
    inner: Inner

    def __init__(self) -> None:
        self.rect = Rect(4, 5)
        self.inner = Inner()

    def get_rect(self) -> Rect:
        return self.rect

    @property
    def prect(self) -> Rect:
        return self.rect


class Num(ValueType):
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def area(self) -> int32:
        return self.v

    def grow(self) -> None:
        pass


class NumBox:
    n: Num

    def __init__(self) -> None:
        self.n = Num(1)

    def replace(self) -> None:
        self.n = Num(9)


def f_area(x: HasArea) -> int32:
    return x.area()


def f_grow(x: Growable) -> None:
    x.grow()


def outer_grow(x: Growable) -> None:
    # Nested callee: the parameter is handed on to another protocol slot.
    f_grow(x)


class Meter:
    def __init__(self) -> None:
        pass

    def area_of(self, x: HasArea) -> int32:
        return x.area()

    def grow_it(self, x: Growable) -> None:
        # Mutates only through the parameter: the method is const, the
        # parameter is not.
        x.grow()

    @staticmethod
    def s_grow(x: Growable) -> None:
        x.grow()


class Grower:
    grown: int32

    def __init__(self, x: Growable) -> None:
        # A constructor that mutates only through its protocol parameter.
        x.grow()
        self.grown = 1


class Tagged[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def grow_with(self, x: Growable) -> int32:
        x.grow()
        return len(self.items)


def sum_span(c: Spannable[int32]) -> int32:
    # An all-@readonly generic protocol iterated: the parameter stays const.
    total = 0
    for x in c:
        total += x
    return total


def replaced_read(x: HasArea, b: NumBox) -> int32:
    # The field holding the argument is replaced before the parameter is read.
    b.replace()
    return x.area()


def gen_grow(x: Growable) -> Iterator[int32]:
    x.grow()
    yield 1
    x.grow()
    yield 2


async def async_grow(x: Growable) -> int32:
    x.grow()
    return 3


@overload
def ov_grow(x: Growable) -> int32: ...
@overload
def ov_grow(x: Growable, n: int32) -> int32: ...
def ov_grow(x: Growable, n: int32 = 1) -> int32:
    x.grow()
    return n


class OvMeter:
    def __init__(self) -> None:
        pass

    @overload
    def area_of(self, x: HasArea) -> int32: ...
    @overload
    def area_of(self, x: HasArea, n: int32) -> int32: ...
    def area_of(self, x: HasArea, n: int32 = 0) -> int32:
        return x.area() + n


class ReadsW(Protocol):
    @readonly
    def width(self) -> int32: ...


class WritesW(ReadsW, Protocol):
    # Redeclares the parent's @readonly method without it.
    def width(self) -> int32: ...


class PeeksW(Protocol):
    def width(self) -> int32: ...


class SeesW(PeeksW, Protocol):
    # Redeclares the parent's mutable method as @readonly.
    @readonly
    def width(self) -> int32: ...


class Wide:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w

    @readonly
    def width(self) -> int32:
        return self.w


class OvReader:
    def __init__(self) -> None:
        pass

    # A method group's specialization reads its const off the group's facts:
    # a fresh argument binds the `const` parameter of each.
    @overload
    def w_of(self, x: ReadsW) -> int32: ...
    @overload
    def w_of(self, x: ReadsW, n: int32) -> int32: ...
    def w_of(self, x: ReadsW, n: int32 = 0) -> int32:
        return x.width() + n


def through_writes(x: WritesW) -> int32:
    return x.width()


def through_sees(x: SeesW) -> int32:
    return x.width()


class Cell:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w

    def get(self) -> int32:
        return self.w


def bump_span(xs: Spannable[Cell]) -> None:
    # Writes the caller's elements, as CPython does; the element should be
    # readonly (BUGS.md#spannable-element-ignores-return-readonly).
    for q in xs:
        q.w += 1


def read_span(xs: Spannable[Cell]) -> int32:
    # An inferred-const method called on the element.
    t = 0
    for q in xs:
        t += q.get()
    return t


def first[T: AnyFixedInt](xs: Spannable[T]) -> T:
    # A numeric element is a copy.
    for q in xs:
        return q
    raise ValueError("empty")


@overload
@readonly
def ro_area(x: HasArea) -> int32: ...
@overload
@readonly
def ro_area(x: HasArea, n: int32) -> int32: ...
def ro_area(x: HasArea, n: int32 = 0) -> int32:
    return n


def m_len(x: Sized) -> int32:
    return len(x)


class Lens:
    def __init__(self) -> None:
        pass

    def m_len(self, x: Sized) -> int32:
        return len(x)


def s_free() -> None:
    # Free function: fields, elements, accessors and fresh records.
    h = Holder()
    xs = [Rect(1, 2), Rect(3, 4)]
    d = {"a": Rect(5, 6)}
    print("free area", f_area(h.rect), f_area(h.inner.rect), f_area(xs[1]),  # tpyc: ok
          f_area(d["a"]), f_area(h.get_rect()), f_area(h.prect))  # tpyc: ok
    f_grow(h.rect)  # tpyc: ok
    f_grow(h.inner.rect)  # tpyc: ok
    f_grow(xs[1])  # tpyc: ok
    f_grow(d["a"])  # tpyc: ok
    f_grow(h.get_rect())  # tpyc: ok
    f_grow(h.prect)  # tpyc: ok
    print("free grow", h.rect.w, h.inner.rect.w, xs[1].w, d["a"].w)
    print("free fresh", f_area(Rect(8, 9)))  # tpyc: ok
    f_grow(Rect(1, 1))  # tpyc: ok


def s_value() -> None:
    # A value-type record at a protocol slot is copied, as at its concrete
    # parameter: replacing the field holding it does not reach the callee.
    n = Num(3)
    print("value", f_area(Num(8)), f_area(n))  # tpyc: ok
    f_grow(n)  # tpyc: ok
    f_grow(Num(1))  # tpyc: ok
    b = NumBox()
    print("value replaced", replaced_read(b.n, b), b.n.v)  # tpyc: ok


def s_resumable() -> None:
    # A generator and an async callee bind a field and an element.
    h = Holder()
    xs = [Rect(1, 2)]
    for v in gen_grow(h.rect):  # tpyc: ok
        print("gen", v, h.rect.w)
    print("gen elem", list(gen_grow(xs[0])), xs[0].w)  # tpyc: ok
    print("async", asyncio.run(async_grow(h.rect)), h.rect.w)  # tpyc: ok
    print("async elem", asyncio.run(async_grow(xs[0])), xs[0].w)  # tpyc: ok


def s_overload() -> None:
    # An @overload stub with a structural protocol parameter (a stub declares
    # its own signature, so a field or element argument keeps its rows).
    r = Rect(1, 2)
    print("overload", ov_grow(r), ov_grow(r, 5), ov_grow(Rect(3, 3)), r.w)  # tpyc: ok
    m = OvMeter()
    print("overload method", m.area_of(Rect(2, 2)), m.area_of(r, 1))  # tpyc: ok
    print("overload const", OvReader().w_of(Wide(4)), OvReader().w_of(Wide(5), 1))  # tpyc: ok


def s_span() -> None:
    # Iterating a Spannable parameter.
    cs = [Cell(1), Cell(2)]
    bump_span(cs)  # tpyc: ok
    print("span", cs[0].w, cs[1].w, read_span(cs), first([7, 8]))  # tpyc: ok


def s_declared() -> None:
    # @readonly overload stubs: a fresh argument binds the const parameter.
    print("declared", ro_area(Rect(1, 1)), ro_area(Rect(1, 1), 2))  # tpyc: ok


def s_redeclared() -> None:
    # A child protocol redeclaring its parent's method decides the const.
    w = Wide(6)
    print("redeclared", through_writes(w), through_sees(w), through_sees(Wide(7)))  # tpyc: ok


def s_method() -> None:
    # Method, static method and nested callee.
    h = Holder()
    xs = [Rect(1, 2), Rect(3, 4)]
    d = {"a": Rect(5, 6)}
    m = Meter()
    print("method area", m.area_of(h.rect), m.area_of(xs[0]), m.area_of(d["a"]))  # tpyc: ok
    m.grow_it(h.rect)  # tpyc: ok
    m.grow_it(xs[0])  # tpyc: ok
    m.grow_it(d["a"])  # tpyc: ok
    m.grow_it(h.prect)  # tpyc: ok
    print("method grow", h.rect.w, xs[0].w, d["a"].w)
    Meter.s_grow(h.inner.rect)  # tpyc: ok
    Meter.s_grow(xs[0])  # tpyc: ok
    print("static grow", h.inner.rect.w, xs[0].w)
    outer_grow(h.get_rect())  # tpyc: ok
    outer_grow(d["a"])  # tpyc: ok
    print("nested grow", h.rect.w, d["a"].w)


def s_ctor() -> None:
    # Constructor and generic-record method.
    h = Holder()
    xs = [Rect(1, 2)]
    g = Grower(h.rect)  # tpyc: ok
    g2 = Grower(xs[0])  # tpyc: ok
    print("ctor grow", g.grown + g2.grown, h.rect.w, xs[0].w)
    t = Tagged[str]()
    print("generic grow", t.grow_with(h.inner.rect), t.grow_with(xs[0]),  # tpyc: ok
          h.inner.rect.w, xs[0].w)


class Loud:
    n: int32

    def __init__(self, n: int32) -> None:
        print("cond built", n)
        self.n = n

    def area(self) -> int32:
        return self.n


def s_cond(b: bool) -> bool:
    # A fresh record in a conditionally evaluated operand is built only
    # when the operand runs.
    return b or f_area(Loud(7)) > 0  # tpyc: ok


def s_kept() -> None:
    # Arguments this slot does not take keep their own forms.
    d = {"a": 1, "b": 2}
    print("kept", m_len("hello"), Lens().m_len([1, 2]), m_len(d.keys()))  # tpyc: ok
    ys = [1, 2, 3]
    print("span", sum_span(ys))  # tpyc: ok


def main() -> None:
    s_free()
    s_value()
    s_resumable()
    s_overload()
    s_redeclared()
    s_span()
    s_declared()
    s_method()
    s_ctor()
    c1 = s_cond(True)
    c2 = s_cond(False)
    print("cond", c1, c2)
    s_kept()


main()
