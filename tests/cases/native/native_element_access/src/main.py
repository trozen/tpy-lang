# An element-typed @native record's subscripts answer as its dunder calls:
# one declaring the element-access contract (`element_access=...`) renders
# its binding, and an index proven in range the unchecked twin of its own
# access (a loop body that may change the length, or a bound from another
# record, leaves it unproven); one declaring nothing renders its binding as a call, proven or
# not, for a read, a store and a del alike. A store into a contract-declaring
# record is not lowered yet (BUGS.md#native-element-store-rejects).
__tpy__ = {"includes": ["native_types.hpp"]}
from typing import Iterator
from tpy import int32, Own, NativeIterable, pure, readonly, auto_readonly, dispatch
from tpy.extern import native, cpp_template


@native("ne::Cell")
class Cell:
    n: int32


@native("ne::Ring", elements=True)
class Ring[T](NativeIterable[T]):
    def __init__(self) -> None: ...

    @native("tpy::__iter__", function=True)
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    # One twin per access, each getter variant taking its own: the readonly
    # one reads through a const receiver (`std::as_const`), so the render
    # shows which was picked. A generic record's twin spells its type
    # parameter (`{T}`), resolved at the receiver as the call's own binding
    # is.
    @dispatch
    @cpp_template("::ne::raw<{T}>(std::as_const({self}), {0})")
    @pure
    @readonly
    def unchecked_get(self, index: int32) -> T: ...

    @dispatch
    @cpp_template("::ne::raw<{T}>({self}, {0})")
    def unchecked_get(self, index: int32) -> T: ...

    @cpp_template("{self}.data[static_cast<std::size_t>({0})] = {1}")
    def unchecked_set(self, index: int32, value: Own[T]) -> None: ...

    @native("at", element_access=unchecked_get)
    @pure
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...

    @native("put", element_access=unchecked_set)
    def __setitem__(self, index: int32, value: Own[T]) -> None: ...

    # The member `len()` reaches (the runtime's `__len__` calls it), so the
    # proof `0 <= i < len(r)` measures the length the contract names.
    @native("__len__")
    @pure
    @readonly
    def __len__(self) -> int32: ...

    @native("push")
    def push(self, v: Own[T]) -> None: ...


@native("ne::Ring", elements=True)
class Frozen[T](NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    # A readonly-only twin: only the readonly getter variant takes it; its
    # `{cpp}` placeholder spells the element's C++ type.
    @cpp_template("static_cast<const {cpp}&>({self}.data[static_cast<std::size_t>({0})])")
    @pure
    @readonly
    def unchecked_get(self, index: int32) -> T: ...

    @native("at", element_access=unchecked_get)
    @pure
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...

    @native("__len__")
    @pure
    @readonly
    def __len__(self) -> int32: ...


@native("ne::Ring", elements=True)
class Renamed[T](NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @cpp_template("::ne::raw<{T}>({self}, {0})")
    @pure
    @auto_readonly
    def unchecked_get(self, index: int32) -> auto_readonly[T]: ...

    @native("at", element_access=unchecked_get)
    @pure
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...

    # A renamed `__len__` binding: `len(r)` does not run it, so the proof
    # does not measure it and the getter takes no twin.
    @native("size")
    @pure
    @readonly
    def __len__(self) -> int32: ...


@native("ne::Ring<ne::Cell>")
class Measured:
    # The member `len()` reaches, declared on a base.
    @native("__len__")
    @pure
    @readonly
    def __len__(self) -> int32: ...


@native("ne::Ring<ne::Cell>")
class InheritsLen(Measured):
    @cpp_template("::ne::raw<::ne::Cell>(std::as_const({self}), {0})")
    @pure
    @readonly
    def unchecked_get(self, index: int32) -> Cell: ...

    @native("at", element_access=unchecked_get)
    @pure
    @readonly
    def __getitem__(self, index: int32) -> Cell: ...


@native("ne::Ring<ne::Cell>")
class RenamedBase:
    @native("size")
    @pure
    @readonly
    def __len__(self) -> int32: ...


@native("ne::Ring<ne::Cell>")
class InheritsRenamed(RenamedBase):
    @cpp_template("::ne::raw<::ne::Cell>(std::as_const({self}), {0})")
    @pure
    @readonly
    def unchecked_get(self, index: int32) -> Cell: ...

    @native("at", element_access=unchecked_get)
    @pure
    @readonly
    def __getitem__(self, index: int32) -> Cell: ...


@native("ne::Ring", elements=True)
class Plain[T](NativeIterable[T]):
    def __init__(self) -> None: ...

    @native("tpy::__iter__", function=True)
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("at")
    @readonly
    def __getitem__(self, index: int32) -> T: ...

    @native("put")
    def __setitem__(self, index: int32, value: Own[T]) -> None: ...

    @native("size")
    @pure
    @readonly
    def __len__(self) -> int32: ...

    @native("push")
    def push(self, v: Own[T]) -> None: ...


@native("ne::Keys")
class Keys:
    erased: str
    seen: str

    def __init__(self) -> None: ...
    @native("erase")
    def __delitem__(self, key: Own[str]) -> None: ...
    @native("get")
    def __getitem__(self, key: Own[str]) -> int32: ...
    @native("put")
    def __setitem__(self, key: Own[str], v: int32) -> None: ...


@native("ne::cell_ring")
def cell_ring(n: int32) -> Ring[Cell]: ...


@native("ne::cell_frozen")
def cell_frozen(n: int32) -> Frozen[Cell]: ...


@native("ne::cell_renamed")
def cell_renamed(n: int32) -> Renamed[Cell]: ...


@native("ne::cell_inherits_len")
def cell_inherits_len(n: int32) -> InheritsLen: ...


@native("ne::cell_inherits_renamed")
def cell_inherits_renamed(n: int32) -> InheritsRenamed: ...


@native("ne::cell_plain")
def cell_plain(n: int32) -> Plain[Cell]: ...


@native("ne::int_plain")
def int_plain(n: int32) -> Plain[int32]: ...


def contract(r: Ring[Cell], k: int32) -> int32:
    # The contract-declaring record: an element read renders its binding.
    return r[k].n  # tpyc: ok


def total(r: Ring[Cell]) -> int32:
    t = 0
    for j in range(len(r)):
        # A read proven in range renders the `unchecked_get` twin.
        t += r[j].n  # tpyc: bounds_safe(r)
    return t


def readonly_total(r: readonly[Ring[Cell]]) -> int32:
    t = 0
    for j in range(len(r)):
        # A readonly receiver: the readonly getter variant and its own twin.
        t += r[j].n  # tpyc: bounds_safe(r)
    return t


def setn(c: Cell, v: int32) -> None:
    c.n = v


def bump(r: Ring[Cell]) -> None:
    for j in range(len(r)):
        # A proven element passed to a mutating parameter: the mutable
        # getter variant's own (mutable) twin.
        setn(r[j], 7)  # tpyc: bounds_safe(r)


def frozen_total(r: readonly[Frozen[Cell]]) -> int32:
    t = 0
    for j in range(len(r)):
        # The readonly getter variant takes the readonly twin.
        t += r[j].n  # tpyc: bounds_safe(r)
    return t


def frozen_bump(r: Frozen[Cell]) -> None:
    for j in range(len(r)):
        # The mutable getter variant has no twin of its access: its
        # checked binding, through which the write builds.
        setn(r[j], 8)  # tpyc: bounds_safe(r)


def renamed_total(r: Renamed[Cell]) -> int32:
    t = 0
    for j in range(len(r)):
        # Proven, yet no twin: the declared `__len__` is not what the
        # proof measured, so the checked binding renders.
        t += r[j].n  # tpyc: bounds_safe(r)
    return t


def inherited_total(r: InheritsLen) -> int32:
    t = 0
    for j in range(len(r)):
        # The `__len__` the proof measures is inherited, and is what
        # `len(r)` runs: the twin renders.
        t += r[j].n  # tpyc: bounds_safe(r)
    return t


def inherited_renamed_total(r: InheritsRenamed) -> int32:
    t = 0
    for j in range(len(r)):
        # An inherited renamed `__len__`: no twin, the checked binding.
        t += r[j].n  # tpyc: bounds_safe(r)
    return t


def grown_total(r: Ring[Cell]) -> int32:
    t = 0
    for j in range(len(r)):
        r.push(Cell(1))
        # The body changes the ring's length, so `j < len(r)` is no longer
        # known: the checked binding renders.
        t += r[j].n  # tpyc: bounds_checked(r)
    return t


def other_len_total(r: Ring[Cell], s: Ring[Cell]) -> int32:
    t = 0
    for j in range(len(s)):
        # Bounded by another ring's length: the checked binding renders.
        t += r[j].n  # tpyc: bounds_checked(r)
    return t


def plain(p: Plain[Cell], k: int32) -> int32:
    # The record declaring nothing: its read is the call it is.
    return p[k].n  # tpyc: ok


def plain_total(p: Plain[Cell]) -> int32:
    t = 0
    for j in range(len(p)):
        # In range, but no contract is declared: still the call.
        t += p[j].n  # tpyc: ok
    return t


def plain_store(p: Plain[int32], k: int32) -> None:
    # A setter declaring nothing is a call, for the plain and the
    # augmented store alike.
    p[k] = 5  # tpyc: ok
    p[k] += 3  # tpyc: ok


def plain_read(p: Plain[int32], k: int32) -> int32:
    # A getter declaring nothing is a call in every value position: bound
    # to a local and returned.
    x = p[k]  # tpyc: ok
    print("plain_read", x)
    return p[k]  # tpyc: ok


def keys_del(m: Keys, key: str) -> None:
    # An `Own[str]` deleter key from a `str` parameter arrives as the
    # explicit call's owned copy.
    del m[key]  # tpyc: ok
    m.__delitem__(key)


def keys_read_store(m: Keys, key: str) -> None:
    # An `Own[str]` getter and setter key from a `str` parameter arrives as
    # the explicit call's owned copy.
    print("keys_read", m[key], m.__getitem__(key))  # tpyc: ok
    m[key] = 4  # tpyc: ok
    m.__setitem__(key, 4)


def keys_of(m: Keys) -> Keys:
    return m


def keys_del_call_recv(m: Keys) -> None:
    # A call-result receiver deletes through that result, as the explicit
    # call does.
    del keys_of(m)["c"]  # tpyc: ok
    keys_of(m).__delitem__("c")


def main() -> None:
    r = cell_ring(5)
    p = cell_plain(6)
    print("contract", contract(r, 0))
    print("plain", plain(p, 0))
    print("total", total(r))
    print("plain_total", plain_total(p))
    print("readonly_total", readonly_total(r))
    bump(r)
    print("bump", r[0].n)
    f = cell_frozen(3)
    print("frozen_total", frozen_total(f))
    frozen_bump(f)
    print("frozen_bump", f[0].n)
    print("renamed_total", renamed_total(cell_renamed(4)))
    print("inherited_total", inherited_total(cell_inherits_len(2)))
    print("inherited_renamed_total",
          inherited_renamed_total(cell_inherits_renamed(3)))
    g = cell_ring(2)
    print("grown_total", grown_total(g), len(g))
    print("other_len_total", other_len_total(r, cell_ring(3)))
    q = int_plain(1)
    plain_store(q, 0)
    print("plain_store", q[0])
    v = plain_read(q, 0)
    print("plain_read returned", v)
    ks = Keys()
    keys_del(ks, "a")
    print("keys_del", ks.erased)
    keys_del_call_recv(ks)
    print("keys_del_call_recv", ks.erased)
    keys_read_store(ks, "bb")
    print("keys_read_store", ks.seen)


main()
