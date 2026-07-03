# A duplicated case over an alias-imported enum must be detected as a
# duplicate (coverage keying is canonical, so the repeat is caught).
from palette import Color as C


def label(c: C) -> str:
    match c:
        case C.RED:
            return "r"
        case C.RED:  # tpyc: error(/duplicate case for 'C.RED'/)
            return "rr"
        case C.GREEN:
            return "g"
        case C.BLUE:
            return "b"


def main() -> None:
    print(label(C.RED))


main()
