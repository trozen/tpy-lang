# Regression test: @native enums print correctly through three runtime paths
# that don't rely on operator<< (which is intentionally not emitted for native
# enums). Covers:
#   - Direct print(e):       gen_print -> ::tpy::__repr__(e) -> EnumUtil
#   - print(Optional[E]):    print_optional_val's enum if-constexpr branch
#   - print(list[E]):        repr_of -> __repr__(T) template via container path
# tpy: include("native_types.hpp")
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

    # Direct print: routes through ::tpy::__repr__(e) in gen_print.
    print(Color.Red)

    # Optional[NativeEnum]: print_optional_val<void, Color>'s enum branch.
    print(maybe(True))
    print(maybe(False))

    # Container of native enum: tpy::detail::print_element -> repr_of ->
    # __repr__(T) template (constrained on is_enum_v && EnumUtil).
    colors: list[Color] = [Color.Red, Color.Green, Color.Blue]
    print(colors)


main()
