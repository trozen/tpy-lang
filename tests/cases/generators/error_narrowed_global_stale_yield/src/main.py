# A narrowed value-Optional module GLOBAL re-read after a yield is
# stale: the caller can rebind the global between next() calls -- the
# same kill as at call sites, reached through the suspension route. The
# surviving first yield is pinned by
# tests/cases/generators/narrowed_value_opt_frame_faces.
from tpy import int32
from typing import Iterator

G: int32 | None = 5


def ints() -> Iterator[int32]:
    if G is not None:
        yield G
        yield G  # tpyc: error(/Type mismatch in yield value/)
    yield -1


def main() -> None:
    for x in ints():
        print(x)


main()
