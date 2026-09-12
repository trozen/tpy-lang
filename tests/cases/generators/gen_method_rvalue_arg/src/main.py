# A resumable generator METHOD receiving a reference-type param by rvalue
# (list literal / constructed record): the arg is materialized as a named
# scope-local the frame can borrow -- for a mutable param, a readonly[T]
# param, and a generator held across statements.

from typing import Iterator
from tpy import int32, readonly


class Rec:
    v: int32

    def __init__(self, v: int32):
        self.v = v


class Lim:
    def first(self, items: list[int32], cap: int32) -> Iterator[int32]:
        n = 0
        for x in items:
            if n >= cap:
                break
            yield x
            n += 1

    def ro_pair(self, xs: readonly[list[int32]]) -> Iterator[int32]:
        yield 0
        for x in xs:
            yield x

    def rec_val(self, r: readonly[Rec]) -> Iterator[int32]:
        yield r.v
        yield r.v + 1

    def dvals(self, d: readonly[dict[str, int32]]) -> Iterator[int32]:
        yield len(d)
        for k in d:
            yield d[k]

    def echo(self, xs: list[int32]) -> Iterator[int32]:
        yield xs[0]
        yield xs[0]


def main() -> None:
    lim = Lim()
    print(sum(lim.first([5, 6, 7], 2)))
    print(sum(lim.ro_pair([1, 2])))
    print(sum(lim.rec_val(Rec(41))))
    print(sum(lim.dvals({"a": 1, "b": 2})))
    g = lim.first([10, 20, 30], 3)
    total = 0
    for x in g:
        total += x
    print(total)
    # The frame borrows the caller's list, no copy: a mutation between
    # pulls is visible to the generator.
    data: list[int32] = [5, 6]
    got: list[int32] = []
    for v in lim.echo(data):
        got.append(v)
        data[0] = 50
    print(got[0], got[1])


main()
