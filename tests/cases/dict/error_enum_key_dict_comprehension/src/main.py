# A dict COMPREHENSION whose key is an enum rides the narrow comprehension slot
# gate, not the wider dict-key shape, and still rejects.
from enum import Enum
from tpy import Int32


class Color(Enum):
    Red = 0
    Green = 1


def count(cs: list[Color]) -> Int32:
    d = {c: 1 for c in cs}  # tpyc: error(/expr.dict_comp/)
    return len(d)


def main() -> None:
    print(count([Color.Red]))


main()
