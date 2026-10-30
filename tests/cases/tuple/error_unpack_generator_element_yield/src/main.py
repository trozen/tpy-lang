# An unpack target bound off a generator's element is that element's
# ephemeral borrow under another name: yielding it is refused, as yielding
# its twin `a = t[0]` is. The refusal of a relay that yields the element
# once is itself the filed false reject BUGS.md#mono-iterator-relay-yield-rejects.
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def gen_pairs(ps: list[P]) -> Iterator[tuple[P, int32]]:
    for p in ps:
        yield (p, p.n)


def firsts(ps: list[P]) -> Iterator[P]:
    for t in gen_pairs(ps):
        a, k = t  # the subject: `a` borrows the generator's current element
        yield a  # tpyc: error(/Cannot yield 'a': it borrows an element of a generator/)


def main() -> None:
    for p in firsts([P(1)]):
        print(p.n)


main()
