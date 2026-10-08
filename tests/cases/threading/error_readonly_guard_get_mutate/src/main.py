# A guard's get() follows the guard's access, like its implicit deref: a
# readonly guard hands out a readonly payload.
from tpy import int32, readonly
from tpy.sync import Mutex, MutexGuard


class A:
    n: int32

    def __init__(self) -> None:
        self.n = 0


def poke(g: readonly[MutexGuard[A]]) -> None:
    g.get().n = 1  # tpyc: error(/readonly/)


def main() -> None:
    m = Mutex[A](A())
    with m.lock() as g:
        poke(g)


main()
