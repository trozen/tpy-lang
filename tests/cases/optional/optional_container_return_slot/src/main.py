# An `Own[container] | None` return slot renders the same `std::optional<T>` a
# record inner does, and binds the same way at the call site. Mutating THROUGH
# the narrowed binding still rejects (a receiver-family gate), so the reads are
# the whole consumer here -- and a read is parity-blind about whether the slot
# moved or copied. The `Box` leg supplies what the reads cannot: `Box` is
# @nocopy, so a copy anywhere on this path is a C++ compile error rather than
# an invisible duplicate.
from tpy import Array, int32, Own
from tplib.box import Box


def mk_list() -> Own[list[int32]]:
    return [1, 2]


def mk_dict() -> Own[dict[str, int32]]:
    return {"a": 1, "b": 2, "c": 3}


def mk_set() -> Own[set[int32]]:
    return {1, 2, 3, 4}


def mk_bytes() -> Own[bytearray]:
    return bytearray(b"abcde")


def mk_array() -> Own[Array[int32, 6]]:
    return [0, 0, 0, 0, 0, 0]


def mk_boxes() -> Own[list[Box[int32]]]:
    return [Box(1), Box(2), Box(3)]


# Each `opt_*` is the subject: the return slot the axis opened.
def opt_list(flag: bool) -> Own[list[int32]] | None:
    if flag:
        return mk_list()
    return None


def opt_dict(flag: bool) -> Own[dict[str, int32]] | None:
    if flag:
        return mk_dict()
    return None


def opt_set(flag: bool) -> Own[set[int32]] | None:
    if flag:
        return mk_set()
    return None


def opt_bytes(flag: bool) -> Own[bytearray] | None:
    if flag:
        return mk_bytes()
    return None


def opt_array(flag: bool) -> Own[Array[int32, 6]] | None:
    if flag:
        return mk_array()
    return None


def opt_boxes(flag: bool) -> Own[list[Box[int32]]] | None:
    if flag:
        return mk_boxes()
    return None


def size(flag: bool) -> int32:
    # The narrowed binding derefs the `std::optional<T>` slot.
    total = 0
    a = opt_list(flag)
    if a is not None:
        total += len(a)
    b = opt_dict(flag)
    if b is not None:
        total += len(b)
    c = opt_set(flag)
    if c is not None:
        total += len(c)
    d = opt_bytes(flag)
    if d is not None:
        total += len(d)
    e = opt_array(flag)
    if e is not None:
        total += len(e)
    # The @nocopy leg: the whole path from `mk_boxes` through the optional
    # slot to this binding has to move.
    f = opt_boxes(flag)
    if f is not None:
        total += len(f)
    return total


def main() -> None:
    print(size(True), size(False))


main()
