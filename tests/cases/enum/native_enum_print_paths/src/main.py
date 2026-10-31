# Regression test: @native enums print correctly through three runtime paths
# that don't rely on operator<< (which is intentionally not emitted for native
# enums). Covers:
#   - Direct print(e):       ::tpy::__str__(e) -> EnumUtil str text
#   - print(Optional[E]):    print_optional_val's enum if-constexpr branch
#   - print(list[E]):        repr_of -> enum __repr__ (`<E.M: value>`) per element
__tpy__ = {"includes": ["native_types.hpp"]}
from enum import Enum, auto
from tpy.extern import native


@native("ns::Color")
class Color(Enum):
    Red = auto()
    Green = auto()
    Blue = auto()


def maybe(present: bool) -> Color | None:
    if present:
        return Color.Red
    return None


def main() -> None:
    # Iteration over the enum class itself (smoke for native).
    for c in Color:
        print(c)

    # Direct print: routes through ::tpy::__str__(e), the str() text.
    print(Color.Red)

    # Optional[NativeEnum]: print_optional_val<void, Color>'s enum branch.
    print(maybe(True))
    print(maybe(False))

    # Container of native enum: tpy::detail::print_element -> repr_of ->
    # the enum __repr__ (`<Color.Red: value>`, the C++ value).
    colors: list[Color] = [Color.Red, Color.Green, Color.Blue]
    print(colors)


main()
