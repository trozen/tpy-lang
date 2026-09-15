# Instantiation-template arguments that are not a plain reference-axis name:
# a NAME whose binding is a VALUE-form native iterable (a `range` local, a
# `Span` param, a str view) binds bare because it already IS a C++ range; an
# `Array` name at its last use binds bare too, because no consuming `__iter__`
# exists to drain; and a container-returning CALL renders inline whether it
# returns by value or by reference (`list(x)` copies either way).
from tpy import int32, Span, Array


# free function: a `range` LOCAL, the shape a direct `list(range(5))` already
# had.
def from_range() -> int32:
    r = range(5)
    c = list(r)  # tpyc: ok
    return len(c)


# free function: the same source into a set.
def set_from_range() -> int32:
    r = range(4)
    s = set(r)  # tpyc: ok
    return len(s)


# free function: a str view iterates as characters. It reads only the length
# because printing the `list[char]` itself diverges from CPython -- a char
# element reprs unquoted (BUGS.md#char-repr-unquoted).
def from_str(s: str) -> int32:
    cs = list(s)  # tpyc: ok
    return len(cs)


# free function: a Span param.
def from_span(sp: Span[int32]) -> int32:
    xs = list(sp)  # tpyc: ok
    return len(xs)


# free function: an `Array` name at its LAST use -- movable, but with no
# consuming `__iter__` to drain, so it binds bare.
def from_array() -> int32:
    arr: Array[int32, 3] = [1, 2, 3]
    xs = list(arr)  # tpyc: ok
    return len(xs)


# free function: a BORROW-returning container call renders inline; `list()`
# copies, so mutating the copy afterwards leaves the source alone.
def borrow(xs: list[int32]) -> list[int32]:
    return xs


def from_borrow_call(src: list[int32]) -> int32:
    a = list(borrow(src))  # tpyc: ok
    a.append(99)
    return len(a) * 10 + len(src)


class Reader:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method: the same range-local source inside a record method body.
    def count(self, n: int32) -> int32:
        r = range(n)
        return len(list(r))  # tpyc: ok


def main() -> None:
    print("range", from_range())
    print("set", set_from_range())
    print("str", from_str("abcd"))
    data = [1, 2, 3]
    print("span", from_span(data))
    print("array", from_array())
    print("borrow", from_borrow_call([1, 2]))
    print("method", Reader("r").count(6))


main()
