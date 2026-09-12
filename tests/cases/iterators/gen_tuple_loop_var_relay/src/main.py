# A relay generator may re-yield a tuple loop var borrowed from an inner
# generator (delegation: the inner generator is materialized in the relay's
# frame and lives for the relay's whole lifetime). The borrow stays an
# alias: mutating through the relayed tuple on one iteration is visible on
# the next yield of the same element. The two-yield sections take the
# RESUMABLE FRAME, where the loop var is a borrow-tuple frame field.
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def gen() -> Iterator[tuple[int32, Box]]:
    items: list[tuple[int32, Box]] = [(1, Box(5))]
    for _ in range(2):
        yield items[0]


def relay() -> Iterator[tuple[int32, Box]]:
    for p in gen():
        yield p


# free generator, TWO yields (frame): the whole borrow-tuple loop var is
# relayed twice per inner pull, still aliasing the inner generator's element.
def relay_twice() -> Iterator[tuple[int32, Box]]:
    for p in gen():  # tpyc: ok
        yield p
        yield p


class Hub:
    # generator METHOD, same shape.
    def relay(self) -> Iterator[tuple[int32, Box]]:
        for p in gen():  # tpyc: ok
            yield p
            yield p


def main() -> None:
    first = True
    for q in relay():
        if first:
            q[1].val = 99
            first = False
        else:
            print(q[1].val)

    # Mutate through the first relayed tuple; the next yield of the same
    # element sees it, so the relay aliases rather than copying.
    seen: list[int32] = []
    n = 0
    for q2 in relay_twice():
        if n == 0:
            q2[1].val = 77
        seen.append(q2[1].val)
        n += 1
    print("free", seen)

    seen_m: list[int32] = []
    m = 0
    for q3 in Hub().relay():
        if m == 0:
            q3[1].val = 88
        seen_m.append(q3[1].val)
        m += 1
    print("method", seen_m)


main()
