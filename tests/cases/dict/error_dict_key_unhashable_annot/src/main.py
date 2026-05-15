# Regression: `dict[NonHashable, V]` rejected at annotation time, not just
# at literals -- closes the empty-`dict()` + `__setitem__` bypass.
from tpy import Int32
from dataclasses import dataclass


@dataclass
class Point:
    x: Int32
    y: Int32


def main() -> None:
    d: dict[Point, Int32] = {}  # tpyc: error(/Point.*cannot be used as a dict key.*missing __hash__/)
    d[Point(1, 2)] = 1
    print(len(d))


main()
