# Methods on a nested enum are not supported yet: the companion would have
# to nest inside the outer record.
from enum import Enum


class Outer:
    class Kind(Enum):  # tpyc: error(/Methods on a nested enum are not supported yet/)
        A = 0

        def label(self) -> str:
            return "x"

    def __init__(self) -> None:
        self.k = Outer.Kind.A


def main() -> None:
    print(Outer().k)


main()
