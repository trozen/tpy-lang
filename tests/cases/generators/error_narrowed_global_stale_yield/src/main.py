# A narrowed value-Optional module GLOBAL re-read after a yield is
# stale: the caller can rebind the global between next() calls -- the
# same kill as at call sites, reached through the suspension route.
from tpy import Int32
from typing import Iterator

G: Int32 | None = 5


def ints() -> Iterator[Int32]:
    if G is not None:
        yield G  # tpyc: ok
        yield G  # tpyc: error(/Type mismatch in yield value/)
    yield -1


def main() -> None:
    for x in ints():
        print(x)


main()
