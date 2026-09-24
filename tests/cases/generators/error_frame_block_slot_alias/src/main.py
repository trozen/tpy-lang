# A generator first bound in a try body inside a loop is not aliased by a name
# that outlives the loop (BUGS.md#block-optional-slot-alias-dangles).
from typing import Iterator

from tpy import int32


def counter(start: int32) -> Iterator[int32]:
    i = start
    while True:
        i += 1
        yield i


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def main() -> None:
    h = counter(1000)
    for r in range(2):
        try:
            g = counter(r * 100)
        except ValueError:
            return
        if r == 0:
            # The subject: g's storage is the try block's, which h outlives.
            h = g  # tpyc: warning(/'h' will not keep the object/) error(/reseat.frame_opt_storage_alias/)
    print(first(h))


main()
