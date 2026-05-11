# @native kwargs (binding=, function=, cpp_return_type=) are not allowed on enums.
from enum import Enum
from tpy.extern import native


@native("ns::E", binding="C")  # tpyc: error(/@native keyword arguments are not allowed/)
class E(Enum):
    A = 0
    B = 1


def main() -> None:
    pass


main()
