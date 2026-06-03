# Multi-yield generator with a static-protocol parameter (`Iterable[int]`).
# The param is captured into the resumable frame as the deduced template arg
# T_it, and the for-loop iterator field is typed against it. The generator
# BORROWS the iterable (does not copy it): mutating the source list after the
# generator is created -- but before lazy iteration starts -- is observed by
# the generator, matching CPython. A copy would miss the appended element.
from typing import Iterator, Iterable


def echo(it: Iterable[int]) -> Iterator[int]:
    for x in it:
        yield x
        yield x


def main() -> None:
    xs = [1, 2]
    g = echo(xs)
    xs.append(3)  # tpyc: warning(/while borrowed/)
    for v in g:
        print(v)


main()
