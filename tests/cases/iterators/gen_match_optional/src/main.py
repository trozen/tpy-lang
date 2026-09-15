# H1: Optional-subject `match` -- a capture binding from the non-None arm is
# read across a yield (the captured inner value is a frame field).
# Also the Optional CHAIN tiers in a generator: when the None arm is not a
# prefix the match leaves the null-split partition, and both chain tiers
# (plain and guarded) carry the resumable dispatch hook, so their arm bodies
# are frame states like any other.
from typing import Iterator, Optional

from tpy import int32


def gen(x: Optional[int]) -> Iterator[int]:
    match x:
        case None:
            yield -1
        case v:
            yield v
            yield v * 2


def chain(x: Optional[int32]) -> Iterator[int32]:
    # The literal arm before the None arm forces the unguarded chain.
    match x:  # tpyc: ok
        case 1:
            yield 1
            yield 11
        case None:
            yield 0
        case _:
            yield 2
    yield 9


def chain_capture(x: Optional[int32]) -> Iterator[int32]:
    # A whole-Optional capture on the chain: in a resumable body the capture
    # is a frame WRITE, not a block-local declaration, and the name is read
    # after a suspension.
    match x:  # tpyc: ok
        case 1:
            yield 1
        case y:
            yield 0
            if y is None:
                yield -1
            else:
                yield y
    yield 9


def chain_guarded(x: Optional[int32], k: bool) -> Iterator[int32]:
    # A guard puts it on the standalone-block chain.
    match x:  # tpyc: ok
        case 1 if k:
            yield 1
        case None:
            yield 0
        case _:
            yield 2
    yield 9


def main() -> None:
    for y in gen(None):
        print(y)
    print("--")
    for y in gen(4):
        print(y)
    for y in chain(1):
        print("chain_one:", y)
    for y in chain(None):
        print("chain_none:", y)
    for y in chain(7):
        print("chain_other:", y)
    for y in chain_capture(1):
        print("cap_one:", y)
    for y in chain_capture(None):
        print("cap_none:", y)
    for y in chain_capture(6):
        print("cap_other:", y)
    for y in chain_guarded(1, True):
        print("guarded_hit:", y)
    for y in chain_guarded(1, False):
        print("guarded_miss:", y)


main()
