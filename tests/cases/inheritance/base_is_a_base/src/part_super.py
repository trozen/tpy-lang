# super().m(...) / Base.m(self, ...) to a builtin or user base's method, at
# plain, readonly, dunder, generator and async bodies.
import asyncio
from typing import Iterator, Protocol, override
from tpy import int32, dynamic, readonly
from part_init import SupTop


class SupL(list[int32]):  # tpyc: warning(/'SupL.append' hides/) warning(/'SupL.__eq__' hides/)
    def append(self, x: int32) -> None:
        super().append(x * 2)  # tpyc: ok

    @readonly
    def size(self) -> int32:
        return super().__len__()  # tpyc: ok

    def __eq__(self, other: "SupL") -> bool:
        # spurious (BUGS.md#builtin-dunder-arg-warning-false-positive)
        return super().__eq__(other)  # tpyc: warning(/'SupL' passed as 'list.int32.'/)

    def twice(self) -> Iterator[int32]:
        yield super().__len__()  # tpyc: ok


class SupD(dict[str, int32]):  # tpyc: warning(/'SupD.__setitem__' hides/)
    def __setitem__(self, k: str, v: int32) -> None:
        super().__setitem__(k, v + 1)  # tpyc: ok


class SupM(SupL):  # tpyc: warning(/'SupM.append' hides 'SupL.append'/)
    def append(self, x: int32) -> None:
        super().append(x + 1)  # tpyc: ok


class SupU(list[int32]):  # tpyc: warning(/'SupU.append' hides/)
    def append(self, x: int32) -> None:
        list.append(self, x + 100)  # tpyc: ok


class SupBag[T](list[T]):  # tpyc: warning(/'SupBag.append' hides/)
    def append(self, x: T) -> None:
        super().append(x)  # tpyc: warning(/may copy T/)


class SupP:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __add__(self, o: int32) -> int32:
        return self.v + o


class SupQ(SupP):  # tpyc: warning(/'SupQ.__add__' hides 'SupP.__add__'/)
    def __add__(self, o: int32) -> int32:
        return super().__add__(o) * 10  # tpyc: ok


# super().m(...) / Base.m(self, ...) to a builtin base's method runs it on
# the base part of self, at a plain, readonly, dunder and generator body
def super_builtin() -> None:
    a = SupL()
    a.append(3)
    b = SupL()
    b.append(3)
    print("super-append", a[0], len(a), a.size(), list(a.twice()))
    print("super-eq", a == b)
    b.append(1)
    print("super-eq-after", a == b, len(b))
    d = SupD()
    d["k"] = 4
    print("super-setitem", d["k"], len(d))
    m = SupM()
    m.append(5)
    # SupTop's two-level super().append is defined in part_init, beside its base LBare.
    t = SupTop()
    t.append(1)
    print("super-two-level", m[0], len(m), t[0], len(t))
    u = SupU()
    u.append(1)
    g = SupBag[int32]()
    g.append(7)
    print("super-unbound", u[0], len(u), "super-generic", g[0], len(g))
    q = SupQ(1)
    print("super-user-dunder", q + 2)


@dynamic
class SupAddable(Protocol):
    def __add__(self, o: int32) -> int32: ...


class SupVBase(SupAddable):
    def __add__(self, o: int32) -> int32:
        return o + 1

    def plus(self, o: int32) -> int32:
        return o + 2


class SupVChild(SupVBase):
    @override
    def __add__(self, o: int32) -> int32:
        # runs the base's body, not this override again (a polymorphic base)
        return super().__add__(o) * 10  # tpyc: ok

    def vals(self) -> Iterator[int32]:
        # in a generator frame the base's body runs on the stored receiver
        yield super().__add__(2)  # tpyc: ok
        yield SupVBase.__add__(self, 3)  # tpyc: ok
        yield super().plus(4)  # tpyc: ok

    async def aval(self) -> int32:
        await asyncio.sleep(0)
        # in an async frame, after a suspension, likewise
        a = super().__add__(5)  # tpyc: ok
        return a + SupVBase.__add__(self, 6)  # tpyc: ok


class ClrL(list[int32]):
    def empty(self) -> int32:
        super().clear()  # tpyc: ok
        return len(self)


class ClrD(dict[str, int32]):
    def empty(self) -> int32:
        super().clear()  # tpyc: ok
        return len(self)


class ClrS(set[int32]):
    def empty(self) -> int32:
        super().clear()  # tpyc: ok
        return len(self)

    def empty_unbound(self) -> int32:
        set.clear(self)  # tpyc: ok
        return len(self)


class SupBox:
    def __init__(self, n: int32) -> None:
        self.n = n


class SupBoxes(list[SupBox]):
    def first(self) -> SupBox:
        # a borrow of self's element: the method stays mutable
        return super().__getitem__(0)  # tpyc: ok

    def first_unbound(self) -> SupBox:
        return list.__getitem__(self, 0)  # tpyc: ok

    def first_n(self) -> int32:
        return self[0].n

    def pin_first(self) -> None:
        # the super() borrow held in a local through self: a write through it
        # reaches self's element
        x = self.first()
        x.n = 9  # tpyc: ok
        print("super-borrow-self", self[0].n)


# super() to a base's dunder through a polymorphic base; a body-less base
# method (`clear`) on the base part of self; a base method's borrow of self
def super_base_part() -> None:
    v = SupVChild()
    print("super-virtual-dunder", v + 2)
    print("super-frame-gen", list(v.vals()))
    print("super-frame-async", asyncio.run(v.aval()))
    l = ClrL()
    l.append(1)
    d = ClrD()
    d["a"] = 1
    s = ClrS()
    s.add(1)
    s2 = ClrS()
    s2.add(2)
    print("super-clear", l.empty(), len(l), d.empty(), len(d), s.empty(),
          len(s), s2.empty_unbound(), len(s2))
    bs = SupBoxes()
    bs.append(SupBox(4))
    bs.first().n = 5
    a = bs.first_n()
    bs.first_unbound().n = 6
    print("super-borrow", a, bs.first_n())
    bs.pin_first()


def main() -> None:
    super_builtin()
    super_base_part()
