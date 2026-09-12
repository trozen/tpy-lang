# isinstance(p, Sub) on a Ptr[@dynamic P] param dispatches via dynamic_cast on
# the underlying P*. The record -> Ptr[P] upcast happens at the call site; the
# true branch narrows to the subclass.
from typing import Protocol
from tpy import int32, Ptr, dynamic, nocopy


@dynamic
class Awaker(Protocol):
    def mark(self) -> int32: ...


@nocopy
class Loud(Awaker):
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def mark(self) -> int32:
        return self.base

    def shout(self) -> int32:
        return self.base * 100


@nocopy
class Quiet(Awaker):
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def mark(self) -> int32:
        return self.base


def reading(p: Ptr[Awaker]) -> int32:
    if isinstance(p, Loud):      # tpyc: ok
        return p.shout()         # subclass-only method, narrowed
    return p.mark()              # virtual protocol dispatch


def main() -> None:
    loud = Loud(3)
    quiet = Quiet(7)
    print(reading(loud))         # record -> Ptr[Awaker] upcast at arg site
    print(reading(quiet))


main()
