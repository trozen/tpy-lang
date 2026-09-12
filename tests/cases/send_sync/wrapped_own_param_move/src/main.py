# A Send[Own[T]] param is representationally a plain Own[T] (the marker erases
# to its inner type in C++), so it must MOVE at its last use. Regression guard:
# forwarding a Send[Own[T]] param into a consuming Own[T] sink used to copy the
# temp (breaking @nocopy). A @nocopy Token makes a silent copy a compile error,
# so this compiling AND running proves the move. The pre-forward borrow proves
# the fix doesn't over-move (only the last use relocates).
from tpy import Own, Send, int32, nocopy
from typing import Protocol


class Counted(Protocol):
    def value(self) -> int32: ...


@nocopy
class Token(Counted):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def value(self) -> int32:
        return self.n


def read_and_keep[T: Counted](item: Own[T]) -> Own[T]:
    return item


def dispatch[T: Counted](item: Send[Own[T]]) -> Own[T]:
    print("dispatching:", item.value())      # borrow (not last use) -- must not move
    return read_and_keep[T](item)             # tpyc: ok -- last use, moves


def main() -> None:
    # Explicit type args: a generic function isn't subscriptable under CPython
    # (see no_cpython.txt), mirroring tpy.thread.spawn's call form.
    kept = dispatch[Token](Token(42))
    print("kept:", kept.value())


main()
