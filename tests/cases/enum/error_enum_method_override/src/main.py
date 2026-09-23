# @override / @auto_readonly do not apply to an enum method: nothing is
# inherited and the member is passed by value.
from enum import Enum
from typing import override


class Color(Enum):
    Red = 0
    Blue = 1

    @override
    def label(self) -> str:  # tpyc: error(/@override .* do not apply to an enum method/)
        return "x"


def main() -> None:
    print(Color.Red)


main()
