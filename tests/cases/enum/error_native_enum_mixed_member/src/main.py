# Mixing native_member() with explicit values is rejected -- native_member
# leaves the value implicit (the C++ side decides), so it can't be combined
# with explicit-value entries.
from enum import Enum
from tpy.extern import native, native_member


@native("ns::E")
class E(Enum):
    A = native_member("A_cpp")
    B = 2  # tpyc: error(/Mixed native_member.*and explicit values/)


def main() -> None:
    pass


main()
