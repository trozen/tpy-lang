# @native enums must be declared at top-level, not nested in a class.
from enum import Enum
from tpy.extern import native


class Outer:
    x: int

    @native("ns::Outer::Inner")
    class Inner(Enum):  # tpyc: error(/@native enum.*cannot be nested/)
        A = 0
        B = 1

    def __init__(self) -> None:
        self.x = 0


def main() -> None:
    pass


main()
