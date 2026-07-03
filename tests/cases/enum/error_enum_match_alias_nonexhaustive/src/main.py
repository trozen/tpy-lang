# A non-exhaustive match over an alias-imported enum must still be
# rejected -- the coverage-keying fix must not over-accept.
from palette import Color as C


def label(c: C) -> str:  # tpyc: error(/can reach the end of the function/)
    match c:
        case C.RED:
            return "r"
        case C.GREEN:
            return "g"


def main() -> None:
    print(label(C.RED))


main()
