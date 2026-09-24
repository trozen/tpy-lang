# A generator object cannot be copied (CPython raises TypeError from copy.copy).
from typing import Iterator

from tpy import int32, copy


def counter(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        i += 1
        yield i


def main() -> None:
    g = counter(3)
    # The subject: copy() of a generator object.
    h = copy(g)  # tpyc: error(/Cannot copy non-copyable type .Iterator\[int32\]./)
    for v in h:
        print(v)


main()
