# An ancestor method that mutates self, called via BaseN.method(self, ...).
# Exercises Phase 2 mutation propagation through the receiver_is_self call edge
# on the synthetic self-rebinding used by the unbound-self dispatch.
from tpy import int32


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = int32(0)

    def bump(self) -> None:
        self.n = self.n + 1


class Wrapper(Counter):
    def bump_twice(self) -> None:
        Counter.bump(self)
        Counter.bump(self)


def main() -> None:
    w = Wrapper()
    w.bump_twice()
    w.bump_twice()
    print(w.n)


main()
