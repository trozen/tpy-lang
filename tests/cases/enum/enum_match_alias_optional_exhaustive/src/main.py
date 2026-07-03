# Regression: an exhaustive match over an Optional alias-imported enum
# (every member plus None) must be accepted -- covers the Optional subject
# path, the second call site the canonical-key fix touches.
from palette import Color as C


def label(c: C | None) -> str:
    match c:
        case C.RED:
            return "r"
        case C.GREEN:
            return "g"
        case C.BLUE:
            return "b"
        case None:
            return "none"


def main() -> None:
    print(label(C.RED))
    print(label(C.GREEN))
    print(label(C.BLUE))
    print(label(None))


main()
