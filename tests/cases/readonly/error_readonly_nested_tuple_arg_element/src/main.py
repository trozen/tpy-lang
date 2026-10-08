# A readonly tuple at a mutable (non-owning) tuple argument is refused at the
# nested reference element, named by its path `1.1`; a borrowing slot gets
# no copy() remedy.
from tpy import int32, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bump(p: tuple[int32, tuple[int32, Box]]) -> None:
    p[1][1].n += 1


def pass_on(t: readonly[tuple[int32, tuple[int32, Box]]]) -> None:
    bump(t)  # tpyc: error(/Cannot pass readonly\[Box\] as mutable Box in argument 'p' \(tuple element 1\.1\)$/)


def main() -> None:
    pass_on((1, (2, Box(3))))


main()
