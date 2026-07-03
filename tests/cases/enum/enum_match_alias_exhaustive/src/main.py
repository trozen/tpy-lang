# Regression: an exhaustive match over an alias-imported enum must be
# accepted (coverage is keyed by the canonical name, not the alias).
from palette import Color as C


def label(c: C) -> str:
    match c:
        case C.RED:
            return "r"
        case C.GREEN:
            return "g"
        case C.BLUE:
            return "b"


def main() -> None:
    print(label(C.RED))
    print(label(C.GREEN))
    print(label(C.BLUE))


main()
