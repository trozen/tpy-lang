# A property setter on an enum has nothing to set: a member is immutable.
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    @property
    def warm(self) -> bool:
        return self == Color.Red

    @warm.setter
    def warm(self, v: bool) -> None:  # tpyc: error(/an enum member is immutable/)
        pass


def main() -> None:
    print(Color.Red.warm)


main()
