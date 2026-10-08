# A write guard's get() follows the guard's access, like its implicit deref: a
# readonly write guard hands out a readonly payload.
from tpy import int32, readonly
from tpy.sync import RwLock, WriteGuard


class A:
    n: int32

    def __init__(self) -> None:
        self.n = 0


def poke(g: readonly[WriteGuard[A]]) -> None:
    g.get().n = 1  # tpyc: error(/readonly/)


def main() -> None:
    lk = RwLock[A](A())
    with lk.write() as g:
        poke(g)


main()
