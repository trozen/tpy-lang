# Native fields written through a Ptr local, a Ptr field and a nested
# field, and a native iterable subclass field iterated through a Ptr local.
__tpy__ = {"includes": ["native_types.hpp"]}
from typing import Iterable, Iterator
from tpy import int32, Ptr, NativeIterable, ValueType, take_ptr
from tpy.extern import native, native_field


@native
class V(ValueType):
    a: int32


@native
def make() -> V: ...


@native
def other() -> V: ...


@native
class H:
    t: V = native_field("t")
    n: int32 = native_field("n")


@native
class M:
    ph: Ptr[H] = native_field("ph")


@native
def find(k: int32) -> Ptr[H]: ...


@native
class Inner:
    t: V = native_field("t")


@native
class Outer:
    inner: Inner = native_field("inner")


@native
def make_outer() -> Outer: ...


@native
class Item:
    id: int32


@native("::Bag")
class Bag[T](Iterable[T], NativeIterable[T]):
    def __iter__(self) -> Iterator[T]: ...


@native
class ItemBag(Bag[Ptr[Item]]):  # tpyc: warning(/hides 'Bag.__iter__'/)
    def __iter__(self) -> Iterator[Ptr[Item]]: ...


@native
class Holder:
    bag: ItemBag = native_field("bag")


@native
def hold(k: int32) -> Ptr[Holder]: ...


def write_ptr_local() -> None:
    p = find(1)
    # a native value-type field through a Ptr local
    p.t = make()  # tpyc: ok
    print("ptr local", p.t.a)


def write_ptr_field(m: M) -> None:
    v = other()
    # ... through a Ptr held in a native field
    m.ph.t = v  # tpyc: ok


def write_nested(o: Outer, s: V) -> None:
    # two native_field hops below the parameter
    o.inner.t = s  # tpyc: ok


def walk() -> None:
    hp = hold(1)
    # a native iterable subclass field through a Ptr local
    for it in hp.bag:  # tpyc: ok
        print("for", it.id)


def main() -> None:
    write_ptr_local()
    m = M(find(1))
    write_ptr_field(m)
    print("ptr field", find(1).t.a)
    o = make_outer()
    s = make()
    write_nested(o, s)
    print("nested", o.inner.t.a)
    walk()


main()
