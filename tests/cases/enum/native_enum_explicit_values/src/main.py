# @native enum where the user spells explicit integer values. These are
# verified against the C++ side via per-member static_assert at compile time.
# If TPy and C++ disagree, the C++ build fails with a clear message rather
# than silently miscompiling.
# tpy: include("native_types.hpp")
from enum import Enum
from tpy.extern import native


@native("ns::Tag")
class Tag(Enum):
    # Values declared explicitly; codegen emits a static_assert per member
    # so this asserts at compile time that they match the C++ side.
    Alpha = 100
    Beta = 200
    Gamma = 300


def main() -> None:
    print(Tag.Alpha.value)
    print(Tag.Beta.value)
    print(Tag.Gamma.value)

    # Tag(100) reads the C++ value via from_value's switch; result is Alpha.
    print(Tag(100))
    print(Tag(200))


main()
