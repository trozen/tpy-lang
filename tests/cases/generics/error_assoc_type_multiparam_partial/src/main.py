# A conformer that pins only SOME of a multi-param protocol's params (first()
# gives K but there is no second() for V) leaves V unresolved, so the call is
# rejected -- distinct params are tracked independently, not broadcast.
# NB the message is the known-suboptimal "cannot infer" rather than "does not
# satisfy bound Pair[K, V]" (tracked as a diagnostic-quality item in BUGS.md).
from typing import Protocol


class Pair[K, V](Protocol):
    def first(self) -> K: ...
    def second(self) -> V: ...


class OnlyFirst:
    a: int

    def __init__(self, a: int):
        self.a = a

    def first(self) -> int:
        return self.a


def take_second[K, V, T: Pair[K, V]](x: T) -> V:
    return x.second()


def main() -> None:
    print(take_second(OnlyFirst(1)))   # tpyc: error(/Cannot infer type arguments for 'take_second'/)


main()
