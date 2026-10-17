# A loan's holder is a binding (by its name) or one loop's running iterator.
# Warned reads and mutations of borrowed storage are guarded by `go` (False).
from typing import Iterator

from tpy import int32, take_ptr


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class N:
    ns: list[int32]

    def __init__(self, v: int32) -> None:
        self.ns = [v, v + 1, v + 2]


# comp_in_iterable: the comprehension's iteration of `p.ns` ends before the
# loop over the fresh `range` starts, so rebinding `p` keeps nothing iterating.
def comp_in_iterable() -> None:
    p = N(1)
    out = []
    for k in range(3):
        j = 0
        for x in range(len([y for y in p.ns])):
            out.append(x)
            j += 1
            if j == 2:
                p = N(10 * (k + 1))  # tpyc: ok
    print("comp_in_iterable", out)


# named_like_iterator: `__for_iter` is an alias of an element like any other
# name, with or without a loop running, and an inner loop's exit keeps it.
def named_like_iterator(xs: list[P], ys: list[int32], go: bool) -> None:
    __for_iter = xs[0]
    for y in ys:
        __for_iter = xs[0]
    xs.append(P(9))  # tpyc: warning(/Mutation of 'xs' while borrowed/)
    if go:
        print("named_like_iterator", __for_iter.v)
    while True:
        __for_iter = xs[0]
        for y in ys:
            pass
        break
    xs.append(P(8))  # tpyc: warning(/Mutation of 'xs' while borrowed/)
    if go:
        print("named_like_iterator", __for_iter.v)
    print("named_like_iterator", len(xs))


def gen(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        yield i
        i += 1


# generator_alias: a generator aliased by `__for_iter` is not run by a loop,
# so deleting its name is accepted.
def generator_alias() -> None:
    g = gen(3)
    __for_iter = g
    print("generator_alias", next(__for_iter, -1))
    del g  # tpyc: ok


# named_like_sink: `__escaped` is a pointer holder like any other name, so
# the rebind that would clobber what it reads is warned.
def named_like_sink(go: bool) -> None:
    other = P(21)
    p = P(22)
    __escaped = take_ptr(other)
    i = 0
    while i < 2:
        p = P(i)  # tpyc: warning(/'__escaped' will not keep the object it was given/)
        if go:
            print("named_like_sink", __escaped.v)
        __escaped = take_ptr(p)
        i += 1
    print("named_like_sink", __escaped.v, p.v)


class Box:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]


# iterating_wording: each mutation of storage a loop runs over says so,
# whatever kind of write it is.
def iterating_wording(xs: list[int32], b: Box, go: bool) -> None:
    for x in xs:
        if go:
            xs[:] = [3]  # tpyc: warning(/Mutation of 'xs' while iterating over it \(slice assignment/)
    for y in b.xs:
        if go:
            b.xs = [4]  # tpyc: warning(/Mutation of 'b.xs' while iterating over it \(field assignment/)
    for z in b.xs:
        if go:
            b.xs += [5]  # tpyc: warning(/Mutation of 'b.xs' while iterating over it \(field assignment/)
    print("iterating_wording", len(xs), len(b.xs))


# outer_loop_kept: an inner `while` that rebinds what the outer loop
# iterates leaves the outer loop's iterator holding its (retargeted) loan.
def outer_loop_kept(rows: list[list[int32]], other: list[int32],
                    go: bool) -> None:
    row = rows[0]
    for x in row:
        while go:
            row = other
            break
        if go:
            rows.append([10])  # tpyc: warning(/Mutation of 'rows' while iterating over it/)
    print("outer_loop_kept", len(rows))


# distinct_elements: each loop's iterator holds its own element's index, so
# growing a third element hits neither and is silent.
def distinct_elements(rows: list[list[int32]]) -> None:
    for x in rows[0]:
        for y in rows[1]:
            rows[2].append(3)  # tpyc: ok
    print("distinct_elements", rows[2])


def main() -> None:
    comp_in_iterable()
    named_like_iterator([P(1)], [1, 2], False)
    generator_alias()
    named_like_sink(False)
    iterating_wording([1, 2], Box(), False)
    outer_loop_kept([[1, 2]], [3], False)
    distinct_elements([[1], [2, 3], []])


main()
