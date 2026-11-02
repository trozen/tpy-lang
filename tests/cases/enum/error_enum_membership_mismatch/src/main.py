# An enum needle against a set of a different enum stays rejected
# (BUGS.md#membership-needle-type-mismatch-rejects: CPython answers False).
from enum import Enum


class Color(Enum):
    Red = 1


class Shape(Enum):
    Box = 1


def main() -> None:
    print(Color.Red in {Shape.Box})  # tpyc: error(/Type mismatch in membership test \(expected Shape\)/)


main()
