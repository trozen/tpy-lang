# A @native enum needle against a @native generic class whose `__contains__`
# binds a free function: the needle renders as the native enum member.
__tpy__ = {"includes": ["native_types.hpp"]}
import enum
from tpy.extern import native


@native("NColor")
class Color(enum.Enum):
    Red = 0
    Green = 1


@native("Bag")
class Bag[T]:
    @native("bag_has", function=True)
    def __contains__(self, value: T) -> bool: ...


@native("Owner")
class Owner:
    colors: Bag[Color]


@native("get_owner")
def get_owner() -> Owner: ...


def has(b: Bag[Color]) -> None:
    # The needle is a @native enum member, the receiver a parameter.
    print(Color.Red in b, Color.Green in b)  # tpyc: ok


def main() -> None:
    o = get_owner()
    has(o.colors)


main()
