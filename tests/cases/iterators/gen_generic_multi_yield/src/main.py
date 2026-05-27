# Generic free-function generator with multiple linear yields (explicit type
# params). Routes onto the resumable frame: the templated struct + __next__ +
# factory emit inline in the header (the legacy struct path can't link an
# out-of-line template __next__).
from typing import Iterator


def two_yields[T](a: T, b: T) -> Iterator[T]:  # tpyc: ok
    yield a
    yield b


def main() -> None:
    # Two instantiations to exercise template monomorphization twice.
    for x in two_yields(1, 2):
        print(x)
    for s in two_yields("x", "y"):
        print(s)


main()
