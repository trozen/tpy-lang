# Over an iterator, max() hands back a COPY of the element: an iterator's
# step may live in the iterator itself (a generator yielding a frame local
# it rebinds, a class returning `self.cur`), so it is valid only until the
# next step and the walk keeps a copy of the best. A write through that copy would be
# lost where CPython writes the element itself, so it is refused. A method
# that writes its receiver (`max(it, key=f).inc()`) is refused the same way;
# `copy(max(...)).inc()` -- an explicit copy -- and binding first (the
# binding warns) are admitted (builtins/borrow_result_iterable).
from typing import Iterator


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: P) -> int:
    return p.v


def walk(ps: list[P]) -> Iterator[P]:
    for p in ps:
        yield p


def main() -> None:
    ps = [P(3), P(9)]
    max(walk(ps), key=key_of).v = 70  # tpyc: error(/is a copy here .*; a write through it is lost/)
    print(ps[1].v)


main()
