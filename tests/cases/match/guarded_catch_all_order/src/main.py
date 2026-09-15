# A GUARDED catch-all arm that PRECEDES a more specific arm keeps its source
# position: Python dispatches arms in order, a C++ switch has none, so a match
# whose label-less arm comes first routes to the ordered chain instead of the
# switch/discriminator partition. One section per dispatch tier that partitions
# arms (enum, fixed-int, the >=5-literal str discriminator, the Optional inner
# dispatch) and per position (method, generator).
from enum import Enum
from typing import Iterator

from tpy import int32


class Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3


def enum_first(c: Color, k: bool) -> int32:
    # The catch-all runs for every subject while the guard holds, GREEN
    # included -- it is not the switch default.
    match c:  # tpyc: ok
        case _ if k:
            return 1
        case Color.GREEN:
            return 2
        case _:
            return 3


def enum_middle(c: Color, k: bool) -> int32:
    # Between two labels: RED still wins over the catch-all, BLUE does not.
    match c:  # tpyc: ok
        case Color.RED:
            return 1
        case _ if k:
            return 2
        case Color.BLUE:
            return 3
        case _:
            return 4


def int_or_group(n: int32, k: bool) -> int32:
    # An or-group with a wildcard alternative IS the catch-all, guard or not.
    match n:  # tpyc: ok
        case 1 | _ if k:
            return 1
        case 2:
            return 2
        case _:
            return 3


def int_or_group_last(n: int32, k: bool) -> int32:
    # Same group with no label after it: order is already safe, so this one
    # keeps the switch.
    match n:  # tpyc: ok
        case 2:
            return 2
        case 1 | _ if k:
            return 1
        case _:
            return 3


def str_table(s: str, k: bool) -> int32:
    # Five literals put the subject on the string discriminator switch; the
    # guarded capture in front of them has to stay in front.
    match s:  # tpyc: ok
        case x if k:
            return len(x)
        case "a":
            return 1
        case "b":
            return 2
        case "c":
            return 3
        case "dd":
            return 4
        case "ee":
            return 5
        case _:
            return 0


def opt_enum(c: Color | None, k: bool) -> int32:
    # The Optional partition's inner dispatch shares the arm walk, so the
    # or-with-wildcard spelling inside it is the same defect.
    match c:  # tpyc: ok
        case None:
            return 0
        case Color.RED | _ if k:
            return 1
        case Color.GREEN:
            return 2
        case _:
            return 3


class Light:
    color: Color

    def __init__(self, color: Color) -> None:
        self.color = color

    def rank(self, k: bool) -> int32:
        # Method position: the demoted chain binds the subject the same way.
        match self.color:  # tpyc: ok
            case _ if k:
                return 1
            case Color.GREEN:
                return 2
            case _:
                return 3


def steps(c: Color, k: bool) -> Iterator[int32]:
    # Generator position: the arm bodies are frame states, and the ordered
    # chain is one of the tiers the dispatch hook admits.
    match c:  # tpyc: ok
        case _ if k:
            yield 1
        case Color.GREEN:
            yield 2
        case _:
            yield 3
    yield 9


def main() -> None:
    print("enum_first:", enum_first(Color.RED, True),
          enum_first(Color.GREEN, True), enum_first(Color.GREEN, False),
          enum_first(Color.BLUE, False))
    print("enum_middle:", enum_middle(Color.RED, True),
          enum_middle(Color.GREEN, True), enum_middle(Color.BLUE, True),
          enum_middle(Color.BLUE, False))
    print("int_or_group:", int_or_group(2, True), int_or_group(2, False),
          int_or_group(7, True), int_or_group(7, False))
    print("int_or_group_last:", int_or_group_last(2, True),
          int_or_group_last(7, True), int_or_group_last(7, False))
    print("str_table:", str_table("a", True), str_table("a", False),
          str_table("ee", False), str_table("zz", False))
    print("opt_enum:", opt_enum(None, True), opt_enum(Color.GREEN, True),
          opt_enum(Color.GREEN, False), opt_enum(Color.BLUE, False))
    print("method:", Light(Color.GREEN).rank(True),
          Light(Color.GREEN).rank(False), Light(Color.BLUE).rank(False))
    for v in steps(Color.GREEN, True):
        print("gen_true:", v)
    for v in steps(Color.GREEN, False):
        print("gen_false:", v)


main()
