# Parenthesized or-pattern groups on an Optional subject, with `None` inside
# the group: the flatten puts the None alternative in the same list the flat
# spelling would produce.
from tpy import Int32


def classify(v: Int32 | None) -> str:
    match v:
        # Same alternatives as the flat `case None | 1 | 2:`.
        case (None | 1) | 2:
            return "none-or-small"
        case 3 | (4 | 5):
            return "medium"
        case _:
            return "other"


def main() -> None:
    print(classify(None))
    print(classify(1))
    print(classify(2))
    print(classify(4))
    print(classify(9))


main()
