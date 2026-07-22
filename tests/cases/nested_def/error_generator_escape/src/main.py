# Returning a nested def from a generator is rejected (frame member has no
# standalone value form).
from typing import Callable, Iterator
from tpy import Int32


def gen() -> Iterator[Callable[[], Int32]]:
    base = 5

    def get() -> Int32:
        return base

    yield get  # tpyc: error(/defined in a generator cannot escape/)


def main() -> None:
    for f in gen():
        print(f())


main()
