# A case pattern from a DIFFERENT enum that merely shares the subject's
# canonical name must be rejected, not silently accepted.
from enum import Enum
from palette import Color as Foreign


class Color(Enum):
    RED = 10
    GREEN = 20


def label(c: Foreign) -> str:
    match c:
        case Color.RED:  # tpyc: error(/does not match/)
            return "r"
        case Color.GREEN:
            return "g"


def main() -> None:
    print(label(Foreign.RED))


main()
