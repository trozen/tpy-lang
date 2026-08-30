# An int literal pattern over an Optional enum subject. The Optional tier tests
# the literal against the inner type, which is an enum with no int comparison.
from enum import Enum
from typing import Optional


class Color(Enum):
    Red = 0
    Green = 1


def describe(c: Optional[Color]) -> str:
    match c:
        case None:
            return "none"
        case 1:  # tpyc: error(/int literal pattern not valid for subject type 'Color'/)
            return "green"
        case _:
            return "other"


def main() -> None:
    print(describe(None))


main()
