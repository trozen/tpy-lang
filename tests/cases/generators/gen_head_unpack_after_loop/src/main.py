# A for-HEAD tuple-unpack target read after the loop, across a yield: the
# head targets are frame locals, so the reads after the loop see the last
# iteration's values, as in CPython. The single-target twin is still open
# (BUGS.md#resumable-for-head-target-read-after-loop).
from typing import Iterator


def g() -> Iterator[str]:
    pairs = [("a", 7), ("b", 9)]
    for name, num in pairs:
        pass
    yield name  # tpyc: ok
    yield str(num)


def main() -> None:
    for v in g():
        print(v)


main()
