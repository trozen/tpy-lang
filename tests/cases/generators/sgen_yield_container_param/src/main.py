# A simple-generator (lambda peephole) yield slot may be a reference-typed
# container, not only an F1 record: the slot is `val_or_ref<C>`, which stores
# a pointer for a non-value payload, so each consumer's mutation lands in the
# caller's own object.
from typing import Iterator
from tpy import Int32, Array


def repeat_list(xs: list[Int32], n: Int32) -> Iterator[list[Int32]]:  # tpyc: ok
    i = 0
    while i < n:
        yield xs  # tpyc: ok
        i += 1


def repeat_dict(d: dict[str, Int32], n: Int32) -> Iterator[dict[str, Int32]]:  # tpyc: ok
    i = 0
    while i < n:
        yield d  # tpyc: ok
        i += 1


def repeat_set(s: set[Int32], n: Int32) -> Iterator[set[Int32]]:  # tpyc: ok
    i = 0
    while i < n:
        yield s  # tpyc: ok
        i += 1


def repeat_buf(b: bytearray, n: Int32) -> Iterator[bytearray]:  # tpyc: ok
    i = 0
    while i < n:
        yield b  # tpyc: ok
        i += 1


def repeat_arr(a: Array[Int32, 2], n: Int32) -> Iterator[Array[Int32, 2]]:  # tpyc: ok
    i = 0
    while i < n:
        yield a  # tpyc: ok
        i += 1


def main() -> None:
    # Every leg mutates THROUGH the yielded value and reads the caller's own
    # object afterwards: a copy at the yield boundary would print the
    # unmutated numbers and CPython would still match on a read-only test.
    xs = [1]
    for got in repeat_list(xs, 2):
        got.append(len(got))
    print(len(xs), xs[1], xs[2])

    d = {"a": 1}
    for gd in repeat_dict(d, 1):
        gd["b"] = 2
    print(len(d), d["b"])

    s = {1}
    for gs in repeat_set(s, 1):
        gs.add(9)
    print(len(s))

    b = bytearray(b"a")
    for gb in repeat_buf(b, 1):
        gb.append(66)
    print(len(b), b[1])

    a = Array[Int32, 2]()
    for ga in repeat_arr(a, 1):
        ga[0] = 7
    print(a[0])


main()
