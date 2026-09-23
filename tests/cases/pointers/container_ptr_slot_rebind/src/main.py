# A container local whose first init is a container-RETURNING call and which is
# later rebound by name, or hoisted out of a try, takes the same pointer-slot
# decl a record local takes -- `T __slot_N = init;` + `T* x = &__slot_N;`, or
# `T* x = &*(__slot_N = init);` for the hoisted flavour. A BORROW-returning
# first init takes the record's other pointer form, `T* x = &(call);`.
from typing import Sized

from tpy import int32, Own, Array


def mk_list() -> Own[list[int32]]:
    return [1]


def mk_dict() -> Own[dict[str, int32]]:
    return {"a": 1}


def mk_set() -> Own[set[int32]]:
    return {1}


def mk_bytes() -> Own[bytearray]:
    return bytearray(b"a")


def mk_array() -> Own[Array[int32, 2]]:
    return [1, 2]


def rebound_list(other: list[int32]) -> None:
    # The rebind aliases `other`, so the append after the boundary is visible
    # through the caller's own binding.
    xs = mk_list()
    xs = other
    xs.append(9)


def rebound_dict(other: dict[str, int32]) -> None:
    d = mk_dict()
    d = other
    d["z"] = 9


def rebound_set(other: set[int32]) -> None:
    s = mk_set()
    s = other
    s.add(9)


def rebound_bytes(other: bytearray) -> None:
    b = mk_bytes()
    b = other
    b.append(9)


def rebound_array(other: Array[int32, 2]) -> None:
    a = mk_array()
    a = other
    a[0] = 9


def longer(a: list[int32], b: list[int32]) -> list[int32]:
    return a if len(a) > len(b) else b


def rebound_borrow(other: list[int32]) -> None:
    # A borrow-returning first init: the pointer takes the callee's lvalue
    # (`&(longer(..))`), the rebind reseats it at `other`, and each append
    # lands in the caller's object it aliased at the time.
    xs = [1, 2, 3]
    ys = [1]
    zs = longer(xs, ys)  # tpyc: ok
    zs.append(7)
    zs = other
    zs.append(9)
    print("borrow_first", xs, ys)


def identity[T: Sized](x: T) -> T:
    return x


def rebound_generic_borrow(other: list[int32]) -> None:
    # the same pointer form off a GENERIC identity's borrow: the rebind
    # reseats it at `other`, the source keeps its length
    src = [4, 5]
    zs = identity(src)  # tpyc: ok
    zs = other
    zs.append(11)
    print("generic_borrow_first", src)


def hoisted() -> int32:
    # The try-block decl hoists to a function-top `std::optional<T>` slot; the
    # later same-name decl fills a second one.
    i = 0
    total = 0
    while i < 2:
        try:
            items = mk_list()
        except Exception:
            break
        total += len(items)
        i += 1
    items = mk_list()
    items.append(5)
    return total + len(items)


def main() -> None:
    xs = [1, 2]
    rebound_list(xs)
    d = {"a": 1}
    rebound_dict(d)
    s = {1}
    rebound_set(s)
    b = bytearray(b"ab")
    rebound_bytes(b)
    a: Array[int32, 2] = [1, 2]
    rebound_array(a)
    rebound_borrow(xs)
    rebound_generic_borrow(xs)
    print(len(xs), len(d), len(s), len(b), a[0])
    print(hoisted())


main()
