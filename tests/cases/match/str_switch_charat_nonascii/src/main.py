# char_at string-switch dispatch with a non-ASCII literal: same-byte-length
# cases force a byte-position discriminator; the UTF-8 lead byte gets a
# numeric case label and must still dispatch correctly.
from tpy import int32


def classify(s: str) -> int32:
    match s:
        case "aa":
            return 1
        case "ba":
            return 2
        case "ca":
            return 3
        case "da":
            return 4
        case "\u00e9":
            return 5
        case _:
            return 0


def main() -> None:
    print(classify("aa"))
    print(classify("\u00e9"))
    print(classify("zz"))


main()
