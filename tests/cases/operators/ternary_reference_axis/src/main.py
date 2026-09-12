# A ternary over two same-typed reference-form NAME arms is itself an lvalue,
# so the result ALIASES the chosen arm: a mutation through it is visible in
# the operand. The admission is the reference axis, so `bytearray` rides it
# beside list/dict/set/Array, and so does a bytearray ternary used as a
# subscript receiver. Each leg mutates after the boundary so a copying render
# would print the wrong number.
from tpy import int32, Array


class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick_list(c: bool, a: list[int32], b: list[int32]) -> None:
    v = a if c else b  # tpyc: ok
    v.append(9)


def pick_dict(c: bool, a: dict[str, int32], b: dict[str, int32]) -> None:
    v = a if c else b  # tpyc: ok
    v["k"] = 9


def pick_set(c: bool, a: set[int32], b: set[int32]) -> None:
    v = a if c else b  # tpyc: ok
    v.add(9)


def pick_bytearray(c: bool, a: bytearray, b: bytearray) -> None:
    # bytearray is a reference type, so its ternary aliases like list's.
    v = a if c else b  # tpyc: ok
    v.append(9)


def pick_array(c: bool, a: Array[int32, 2], b: Array[int32, 2]) -> None:
    v = a if c else b  # tpyc: ok
    v[0] = 9


def pick_record(c: bool, a: Tag, b: Tag) -> None:
    # The record arm the container arm sits above -- it must keep its own
    # route rather than being swallowed by the widened one.
    v = a if c else b  # tpyc: ok
    v.n = 9


def read_through(c: bool, a: bytearray, b: bytearray) -> int32:
    # The same lvalue ternary as a SUBSCRIPT RECEIVER: the element read
    # aliases the chosen buffer.
    return (a if c else b)[0]  # tpyc: ok


def main() -> None:
    xs: list[int32] = [1]
    ys: list[int32] = [2]
    pick_list(True, xs, ys)
    print(len(xs), len(ys))

    d1: dict[str, int32] = {"a": 1}
    d2: dict[str, int32] = {"b": 2}
    pick_dict(False, d1, d2)
    print(len(d1), len(d2))

    s1 = {1}
    s2 = {2}
    pick_set(True, s1, s2)
    print(len(s1), len(s2))

    b1 = bytearray(b"a")
    b2 = bytearray(b"bb")
    pick_bytearray(False, b1, b2)
    print(len(b1), len(b2))
    print(read_through(True, b1, b2))

    a1 = Array[int32, 2]([1, 1])
    a2 = Array[int32, 2]([2, 2])
    pick_array(True, a1, a2)
    print(a1[0], a2[0])

    t1 = Tag(1)
    t2 = Tag(2)
    pick_record(False, t1, t2)
    print(t1.n, t2.n)


main()
