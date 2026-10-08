# set() writes the payload through the guard's pointer, which readonly
# inference cannot see; it is declared non-readonly, so a readonly guard
# cannot overwrite its payload.
from tpy import int32, readonly
from tpy.sync import Mutex, MutexGuard


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def poke(g: readonly[MutexGuard[A]]) -> None:
    g.set(A(42))  # tpyc: error(/Cannot call non-readonly method 'set' on readonly reference/)


def main() -> None:
    m = Mutex[A](A(1))
    with m.lock() as g:
        poke(g)


main()
