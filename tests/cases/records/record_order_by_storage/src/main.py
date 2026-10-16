# A class holding a LATER class by value (field, Optional, tuple, union,
# generic, Array, base, recursive alias) is emitted after it; a nested class
# named before its owner (field, parameter, return, overload, property,
# generator, async) pulls the owner first; heap containers, hash keys, deduced
# returns and members with no C++ declaration add no order.
from __future__ import annotations
from dataclasses import dataclass
import asyncio
from typing import Final, Iterator, overload
from tpy import int32, Array, Ptr, Own, readonly, inline, dispatch
from tpy.extern import cpp_template


# plain field of a later class
class PlainHolder:
    x: PlainB  # tpyc: ok

    def __init__(self) -> None:
        self.x = PlainB(5)


class PlainB:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# Optional of a later class
class OptHolder:
    x: OptB | None  # tpyc: ok

    def __init__(self) -> None:
        self.x = OptB(5)


class OptB:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# field typed as a derived class
class DerBase:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class DerHolder:
    b: DerChild  # tpyc: ok

    def __init__(self) -> None:
        self.b = DerChild(5)


class DerChild(DerBase):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


# tuple element of a later class
class TupHolder:
    t: tuple[TupB, int32]  # tpyc: ok

    def __init__(self) -> None:
        self.t = (TupB(5), 1)


class TupB:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# union of later classes
class UniHolder:
    u: UniB | UniC  # tpyc: ok

    def __init__(self) -> None:
        self.u = UniB(5)


class UniB:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class UniC:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


# a later generic class instantiated at a later class
class GenHolder:
    g: GenBox[GenB]  # tpyc: ok

    def __init__(self) -> None:
        self.g = GenBox(GenB(5))


class GenBox[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v


class GenB:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# @dataclass fields
@dataclass
class DcHolder:
    x: DcB  # tpyc: ok


@dataclass
class DcB:
    n: int32


# readonly field
class RoHolder:
    x: readonly[RoB]  # tpyc: ok

    def __init__(self) -> None:
        self.x = RoB()


class RoB:
    n: int32 = 5


# Array elements are laid out inline
class ArrHolder:
    a: Array[ArrB, 3]  # tpyc: ok

    def __init__(self) -> None:
        self.a = Array[ArrB, 3]()


class ArrB:
    n: int32 = 5


# a nested class's field of a later class
class NestOuter:
    class In:
        x: NestB  # tpyc: ok

        def __init__(self) -> None:
            self.x = NestB(5)

    i: NestOuter.In

    def __init__(self) -> None:
        self.i = NestOuter.In()


class NestB:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# naming a nested class (behind a pointer or a list) needs its owner first
class NameHolder:
    p: Ptr[NameOuter.Inner]  # tpyc: ok
    items: list[NameOuter.Inner]  # tpyc: ok

    def __init__(self) -> None:
        self.p = None
        self.items = []


class NameOuter:
    class Inner:
        n: int32 = 5


# nested siblings: the first holds the second
class SibOuter:
    class First:
        value: SibOuter.Second  # tpyc: ok

        def __init__(self) -> None:
            self.value = SibOuter.Second()

    class Second:
        n: int32 = 5


# later set / dict keys
class KeyHolder:
    keys: set[HKey]  # tpyc: ok
    counts: dict[HKey, int32]  # tpyc: ok

    def __init__(self) -> None:
        self.keys = {HKey(1)}
        self.counts = {HKey(2): 5}


class HKey:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __hash__(self) -> int:
        return hash(self.n)

    def __eq__(self, other: HKey) -> bool:
        return self.n == other.n


# a class keyed on itself
class Node:
    values: set[Node]  # tpyc: ok
    index: dict[Node, int32]  # tpyc: ok
    n: int32

    def __init__(self, n: int32) -> None:
        self.values = set()
        self.index = {}
        self.n = n

    def __hash__(self) -> int:
        return hash(self.n)

    def __eq__(self, other: Node) -> bool:
        return self.n == other.n


# inverse: two classes holding each other through lists keep building
class CycA:
    kids: list[CycB]  # tpyc: ok

    def __init__(self) -> None:
        self.kids = []


class CycB:
    up: list[CycA]  # tpyc: ok

    def __init__(self) -> None:
        self.up = []


# inverse: a hashed key holding its holder through a list keeps building
class HcHolder:
    keys: set[HcKey]  # tpyc: ok

    def __init__(self) -> None:
        self.keys = {HcKey(1)}


class HcKey:
    holders: list[HcHolder]
    n: int32

    def __init__(self, n: int32) -> None:
        self.holders = []
        self.n = n

    def __hash__(self) -> int:
        return hash(self.n)

    def __eq__(self, other: HcKey) -> bool:
        return self.n == other.n


# inverse: a deduced-return body calling a prototype deferred past a later
# class keeps its order
class ProtoBase:
    pass


class ProtoDerived[T](ProtoBase):
    items: list[int32]

    def __init__(self) -> None:
        self.items = [5]

    def __iter__(self) -> Iterator[int32]:
        print("proto call", proto_number(3))  # tpyc: ok
        return iter(self.items)


class ProtoConst:
    N: Final[int32] = 5


def proto_number(n: int32 = ProtoConst.N) -> int32:
    return n


# a method parameter naming a later nested class
class ParamUser:
    def read(self, x: ParamOuter.Inner) -> int32:  # tpyc: ok
        return x.n


class ParamOuter:
    class Inner:
        n: int32 = 5


# an explicit return naming a later nested class
class RetUser:
    def make(self) -> Own[RetOuter.Inner]:  # tpyc: ok
        return RetOuter.Inner()


class RetOuter:
    class Inner:
        n: int32 = 5


# inverse: a deduced return (`auto`) naming a later nested class adds no
# order, so a body calling a prototype deferred past this class keeps building
class DedFirst:
    N: Final[int32] = 5

    def __iter__(self) -> Iterator[DedOuter.Inner]:  # tpyc: ok
        return iter(ded_owner.items)


class DedCaller[T]:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [5]

    def __iter__(self) -> Iterator[int32]:
        print("deduced call", ded_number(3))  # tpyc: ok
        return iter(self.items)


class DedOuter:
    class Inner:
        n: int32 = 5

    items: list[DedOuter.Inner]

    def __init__(self) -> None:
        self.items = []


ded_owner = DedOuter()


def ded_number(n: int32 = DedFirst.N) -> int32:
    return n


# @overload stubs naming a later nested class
class OvlHolder:
    @overload
    def use(self, value: OvlOuter.Inner) -> int32: ...  # tpyc: ok

    @overload
    def use(self, value: int32) -> int32: ...

    def use(self, value: OvlOuter.Inner | int32) -> int32:
        if isinstance(value, OvlOuter.Inner):
            return value.n
        return value


class OvlOuter:
    class Inner:
        n: int32 = 5


# a property naming a later nested class
class PropHolder:
    @property
    def value(self) -> Own[PropOuter.Inner]:  # tpyc: ok
        return PropOuter.Inner()

    @value.setter
    # The setter's parameter takes the getter's type, Own[PropOuter.Inner].
    def value(self, value: PropOuter.Inner) -> None:  # tpyc: warning(/never consumed/)
        print("property set", value.n)


class PropOuter:
    class Inner:
        n: int32 = 5


# a generator method naming a later nested class
class GenmHolder:
    def values(self, value: GenmOuter.Inner) -> Iterator[int32]:  # tpyc: ok
        yield value.n


class GenmOuter:
    class Inner:
        n: int32 = 5


# an async method naming a later nested class
class AsyHolder:
    async def value(self, value: AsyOuter.Inner) -> int32:  # tpyc: ok
        return value.n


class AsyOuter:
    class Inner:
        n: int32 = 5


async def async_section() -> None:
    h = AsyHolder()
    inner = AsyOuter.Inner()
    inner.n += 1
    print("async", await h.value(inner))


# a recursive alias holding a later class inline (declared only: a field of a
# generic recursive alias cannot be assigned yet, so the header is the subject)
type TreeInline[T] = T | list[TreeInline[T]]


class AliasHolder:
    value: TreeInline[AliasLast]  # tpyc: ok


class AliasLast:
    n: int32 = 5


# inverse: a recursive alias storing its argument in a list or behind a
# pointer holds none of it inline, so the order stays
type TreeHeap[T] = int32 | list[T] | list[TreeHeap[T]]
type TreePtr[T] = int32 | Ptr[T] | list[TreePtr[T]]


class HeapOuter:
    class Inner:
        pass

    tree: TreeHeap[HeapLast]  # tpyc: ok
    ptree: TreePtr[HeapLast]  # tpyc: ok

    def __init__(self) -> None:
        self.tree = 1
        self.ptree = 2


class HeapHolder:
    value: Ptr[HeapOuter.Inner]  # tpyc: ok

    def __init__(self) -> None:
        self.value = None


class HeapLast:
    n: int32 = 1


# a later nested hashed key
class NkHolder:
    keys: set[NkOuter.Key]  # tpyc: ok

    def __init__(self) -> None:
        self.keys = {NkOuter.Key(1)}


class NkOuter:
    class Key:
        n: int32

        def __init__(self, n: int32) -> None:
            self.n = n

        def __hash__(self) -> int:
            return hash(self.n)

        def __eq__(self, other: NkOuter.Key) -> bool:
            return self.n == other.n


# inverse: an @inline method and a @cpp_template variant declare nothing in
# C++, so naming a later nested class there adds no order and a body calling
# a prototype deferred past this class keeps building
def inl_sink(n: int32) -> None:
    print("inline sink", n)


class InlFirst:
    N: Final[int32] = 5

    @inline
    def use(self, value: Ptr[InlOuter.Inner]) -> None:  # tpyc: ok
        inl_sink(7)

    @inline
    def get(self) -> Ptr[InlOuter.Inner]:  # tpyc: ok
        inl_sink(8)

    @dispatch
    @cpp_template("static_cast<int32_t>(7)")
    def pick(self, value: Ptr[InlOuter.Inner]) -> int32: ...  # tpyc: ok

    @dispatch
    def pick(self, value: int32) -> int32:
        return value


class InlCaller[T]:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [5]

    def __iter__(self) -> Iterator[int32]:
        print("inline call", inl_number(3))  # tpyc: ok
        return iter(self.items)


class InlOuter:
    class Inner:
        n: int32 = 5


def inl_number(n: int32 = InlFirst.N) -> int32:
    return n


# inverse: a generic class keyed on a later hashed class keeps the key first,
# so its deduced-return body calling a prototype deferred past a class between
# them keeps building
class HbCaller[T]:
    keys: set[HbKey]  # tpyc: ok
    items: list[int32]

    def __init__(self) -> None:
        self.keys = set()
        self.items = [5]

    def __iter__(self) -> Iterator[int32]:
        print("hash baseline call", hb_number(3))  # tpyc: ok
        return iter(self.items)


class HbConst:
    N: Final[int32] = 5


class HbKey:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __hash__(self) -> int:
        return hash(self.n)

    def __eq__(self, other: HbKey) -> bool:
        return self.n == other.n


def hb_number(n: int32 = HbConst.N) -> int32:
    return n


def main() -> None:
    plain = PlainHolder()
    plain.x.n += 1
    print("plain", plain.x.n)

    opt = OptHolder()
    if opt.x is not None:
        opt.x.n += 1
        print("optional", opt.x.n)

    der = DerHolder()
    der.b.n += 1
    print("derived", der.b.n)

    tup = TupHolder()
    tup.t[0].n += 1
    print("tuple", tup.t[0].n, tup.t[1])

    uni = UniHolder()
    u = uni.u
    if isinstance(u, UniB):
        u.n += 1
    v = uni.u
    if isinstance(v, UniB):
        print("union", v.n)

    gen = GenHolder()
    gen.g.v.n += 1
    print("generic", gen.g.v.n)

    dc = DcHolder(DcB(5))
    dc.x.n += 1
    print("dataclass", dc.x.n)

    ro = RoHolder()
    print("readonly", ro.x.n)

    arr = ArrHolder()
    arr.a[1].n += 1
    print("array", arr.a[0].n, arr.a[1].n)

    nest = NestOuter()
    nest.i.x.n += 1
    print("nested", nest.i.x.n)

    name = NameHolder()
    name.items.append(NameOuter.Inner())
    name.items[0].n += 1
    print("naming", name.p is None, name.items[0].n)

    sib = SibOuter.First()
    sib.value.n += 1
    print("siblings", sib.value.n)

    keys = KeyHolder()
    keys.counts[HKey(2)] += 1
    print("keys", len(keys.keys), keys.counts[HKey(2)])

    node = Node(1)
    node.values.add(Node(2))
    node.index[Node(3)] = 7
    print("self key", len(node.values), len(node.index))

    cyc = CycA()
    cyc.kids.append(CycB())
    cyc.kids[0].up.append(CycA())
    print("list cycle", len(cyc.kids), len(cyc.kids[0].up))

    hc = HcHolder()
    print("hash cycle", len(hc.keys))

    for n in ProtoDerived[int32]():
        print("proto", n)

    pu = ParamUser()
    pin = ParamOuter.Inner()
    pin.n += 1
    print("param", pu.read(pin))

    made = RetUser().make()
    made.n += 1
    print("return", made.n)

    ded_owner.items.append(DedOuter.Inner())
    for inner in DedFirst():
        inner.n += 1
    print("deduced owner", ded_owner.items[0].n)
    for n in DedCaller[int32]():
        print("deduced", n)

    ovl = OvlHolder()
    oin = OvlOuter.Inner()
    oin.n += 1
    print("overload", ovl.use(oin), ovl.use(3))

    prop = PropHolder()
    print("property get", prop.value.n)
    pv = PropOuter.Inner()
    pv.n += 1
    prop.value = pv

    genm = GenmHolder()
    gin = GenmOuter.Inner()
    gin.n += 1
    for n in genm.values(gin):
        print("generator", n)

    asyncio.run(async_section())


    heap = HeapOuter()
    heap_holder = HeapHolder()
    print("alias heap", heap_holder.value is None)

    nk = NkHolder()
    nk.keys.add(NkOuter.Key(2))
    print("nested key", len(nk.keys))

    inl = InlFirst()
    inl.use(None)
    inl.get()
    print("cpp_template", inl.pick(4))
    for n in InlCaller[int32]():
        print("inline", n)

    hb = HbCaller[int32]()
    hb.keys.add(HbKey(1))
    for n in hb:
        print("hash baseline", n, len(hb.keys))


main()
