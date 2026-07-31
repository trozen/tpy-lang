# A **kwargs param has no fixed slot, so the call-site fill must skip it the
# same way it skips *args -- otherwise a function combining **kwargs with a
# materialized default is reported as missing an argument.
from typing import TypedDict, Unpack

from tpy import Int64, ValueType


class Fixed(ValueType):
    off: Int64

    def __init__(self, off: Int64) -> None:
        self.off = off


class Options(TypedDict):
    host: str


def probe(x: "Fixed | None" = None, **kwargs: Unpack[Options]) -> Int64:
    return -1 if x is None else x.off


def spread(a: Int64, b: Int64 = 4, *rest: Int64, tag: Int64 = 9) -> Int64:
    total = a * 1000 + b * 100 + tag
    for r in rest:
        total = total + r
    return total


def main() -> None:
    print(probe(Fixed(3), host="a"))
    print(probe(host="b"))
    # `spread(1)` is deliberately absent: with *args present, omitting b so
    # tag's default must skip a positional slot still mis-binds, so pinning it
    # here would freeze a wrong value.
    print(spread(1, 2))
    print(spread(1, 2, 5, 6))


main()
