# An enum method cannot be named `self`: the compiled enum holds its member
# under that name.
from enum import Enum


class Color(Enum):
    Red = 0

    def self(self) -> str:  # tpyc: error(/an enum method cannot be named 'self'/)
        return "x"


def main() -> None:
    print(Color.Red)


main()
