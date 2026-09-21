# A comprehension or generator expression whose SOURCE (or whose element
# expression) reads a name narrowed by an enclosing `isinstance`. The lowered
# body reads the extraction alias, so a genexpr captures the narrowed VALUE --
# the body must never refer to the union under a name it was not handed. The source ALIASES the caller's container, so the last
# section mutates it after the call and reads the sum back.
from typing import Iterator

from tpy import Own, int32


# genexpr over a narrowed source, in a free function
def total(u: list[int32] | bytearray) -> int32:
    if isinstance(u, list):
        return sum(x for x in u)  # tpyc: ok
    return len(u)


# list comprehension over the same narrowed source (a statement-expression,
# so it captures nothing -- the sibling the genexpr gate shares a route with)
def doubled(u: list[int32] | bytearray) -> Own[list[int32]]:
    if isinstance(u, list):
        return [x * 2 for x in u]  # tpyc: ok
    return [0]


# the ELEMENT expression reads a SECOND narrowed name
def scaled(u: list[int32] | bytearray, w: list[int32] | bytearray) -> int32:
    if isinstance(u, list):
        if isinstance(w, list):
            return sum(x * len(w) for x in u)  # tpyc: ok
        return len(w)
    return 0


# a RANGE-sourced genexpr whose element reads a narrowed name: the same
# capture, on the path that never had a source-name gate to reject it
def ranged(w: list[int32] | bytearray) -> int32:
    if isinstance(w, list):
        return sum(i * len(w) for i in range(3))  # tpyc: ok
    return 0


# the same genexpr inside a generator frame (the capture is a frame member)
def gen(u: list[int32] | bytearray) -> Iterator[int32]:
    if isinstance(u, list):
        yield sum(x for x in u)  # tpyc: ok
        yield sum(x + 1 for x in u)


def main() -> None:
    xs: list[int32] = [1, 2, 3]
    u: list[int32] | bytearray = xs
    print("total", total(u))
    print("doubled", doubled(u))
    print("scaled", scaled(u, u))
    print("ranged", ranged(u))
    for v in gen(u):
        print("gen", v)

    ba = bytearray(b"ab")
    b: list[int32] | bytearray = ba
    print("other arm", total(b), scaled(b, b), ranged(b))

    # the narrowed source aliases the caller's list
    xs[0] = 10
    print("after", total(u))


main()
