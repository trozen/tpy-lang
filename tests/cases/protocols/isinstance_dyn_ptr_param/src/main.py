# isinstance(p, Sub) on a Ptr[@dynamic P] param dispatches via dynamic_cast on
# the underlying P*. The record -> Ptr[P] upcast happens at the call site; the
# true branch narrows to the subclass.
from typing import Protocol
from tpy import Int32, Ptr, dynamic, nocopy


@dynamic
class Awaker(Protocol):
    def mark(self) -> Int32: ...


@nocopy
class Loud(Awaker):
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def mark(self) -> Int32:
        return self.base

    def shout(self) -> Int32:
        return self.base * 100


@nocopy
class Quiet(Awaker):
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def mark(self) -> Int32:
        return self.base


def reading(p: Ptr[Awaker]) -> Int32:
    if isinstance(p, Loud):      # tpyc: ok
        return p.shout()         # subclass-only method, narrowed
    return p.mark()              # virtual protocol dispatch


def main() -> None:
    loud = Loud(3)
    quiet = Quiet(7)
    print(reading(loud))         # record -> Ptr[Awaker] upcast at arg site
    print(reading(quiet))


main()
