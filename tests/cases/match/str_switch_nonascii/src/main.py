# String-switch dispatch (5+ unguarded cases) with a non-ASCII literal:
# buckets are computed over UTF-8 bytes, so "caf\u00e9" (4 codepoints,
# 5 bytes) must reach its arm instead of falling to the wildcard.
from tpy import int32


def classify(s: str) -> int32:
    match s:
        case "x":
            return 1
        case "xy":
            return 2
        case "xyz":
            return 3
        case "wxyz":
            return 4
        case "caf\u00e9":
            return 5
        case _:
            return 0


def main() -> None:
    print(classify("x"))
    print(classify("caf\u00e9"))
    print(classify("nope"))


main()
