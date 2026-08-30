# Parenthesized or-pattern groups on a single-record subject, where each
# alternative carries a field-value sub-pattern (the record if/elif strategy).
from dataclasses import dataclass


@dataclass
class P:
    a: int


def classify(p: P) -> str:
    match p:
        # Same alternatives as the flat `case P(a=1) | P(a=2) | P(a=3):`.
        case P(a=1) | (P(a=2) | P(a=3)):
            return "low"
        case P(a=4):
            return "four"
        case _:
            return "high"


def main() -> None:
    print(classify(P(1)))
    print(classify(P(2)))
    print(classify(P(3)))
    print(classify(P(4)))
    print(classify(P(9)))


main()
