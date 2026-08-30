# A guarded int literal over an enum subject. The guard routes the arm through
# a different dispatch tier, which must reject the literal just the same.
from enum import Enum


class Color(Enum):
    Red = 0
    Green = 1


def describe(c: Color, flag: bool) -> str:
    match c:
        case 1 if flag:  # tpyc: error(/int literal pattern not valid for subject type 'Color'/)
            return "green"
        case _:
            return "other"


def main() -> None:
    print(describe(Color.Red, True))


main()
