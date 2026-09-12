# Parenthesized or-pattern groups on an int subject (the primitive switch
# strategy): the group flattens into one set of switch labels.
from tpy import int32


def classify(n: int32) -> str:
    match n:
        # Same labels as the flat `case 1 | 2 | 3:`.
        case (1 | 2) | 3:
            return "small"
        case 4 | (5 | 6):
            return "medium"
        case _:
            return "big"


def main() -> None:
    print(classify(1))
    print(classify(2))
    print(classify(3))
    print(classify(4))
    print(classify(5))
    print(classify(6))
    print(classify(7))


main()
