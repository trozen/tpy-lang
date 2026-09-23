# Methods on a @native enum are rejected: a @native type is declaration-only,
# as for a @native class.
from enum import Enum, auto
from tpy.extern import native


@native("ns::E")
class E(Enum):
    A = auto()
    B = auto()

    def label(self) -> str:  # tpyc: error(/Methods on @native enum 'E' are not supported/)
        return "first" if self == E.A else "second"


def main() -> None:
    print(E.A)


main()
