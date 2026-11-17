# docs/LANGUAGE_FEATURES.md "An owning iterable is traversed once": an owning
# sub-protocol parameter with a member beyond iteration (`__len__`) is still
# traversed once, so reading it after its loop is an error, as for
# `Iterable[Own[R]]` (read the member before the loop instead).
from typing import Iterable, Protocol
from tpy import Own, int32


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


class Counted[T](Iterable[T], Protocol):
    def __len__(self) -> int32: ...


def total(xs: Counted[Own[R]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    # The member read after the one traversal is the subject.
    return t + len(xs)  # tpyc: error(/Cannot use 'xs' after it was consumed/)


def main() -> None:
    print(total([R(1), R(2)]))


main()
