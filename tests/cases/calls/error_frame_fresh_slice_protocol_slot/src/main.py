# A slice of a fresh str at a structural `Iterable[char]` parameter a
# generator head keeps is refused (BUGS.md#frame-fresh-slice-protocol-slot-refused).
from typing import Iterable, Iterator
from tpy import char, int32


def chars(xs: Iterable[char]) -> Iterator[char]:
    for x in xs:
        yield x


def make(n: int32) -> str:
    return "  " + "abcdefgh" * n + "  "


def gen() -> Iterator[str]:  # tpyc: error(/call.arg_shape.protocol.static/)
    # the slice is a view into the fresh string, which no frame holder owns
    for c in chars(make(40)[2:50]):
        yield str(c)


def main() -> None:
    out = ""
    for c in gen():
        out += c
    print(out)


main()
