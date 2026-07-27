# A walrus in an always-true truthiness position. The operand's render carries
# the ASSIGNMENT, so folding the operand away dropped the binding and left the
# target default-constructed -- a silently wrong VALUE, not merely a missing
# side effect. Both always-true arms are covered: a plain record and a plain
# enum.
from enum import Enum

from tpy import Own


class Color(Enum):
    RED = 1
    GREEN = 2


class Rec:
    v: int

    def __init__(self, v: int):
        self.v = v


def make(n: int) -> Own[Rec]:
    return Rec(n)


def pick() -> Color:
    return Color.GREEN


def main() -> None:
    if (r := make(7)):
        print("record walrus:", r.v)

    if (c := pick()):
        print("enum walrus:", c.name)


main()
