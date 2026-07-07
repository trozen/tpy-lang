# Enum member as a default parameter value, on a free function and a method,
# called with the default and with an explicit override. The default lowers to
# the scoped C++ enumerator; behavior matches CPython (enum members are
# immutable singletons, so there is no copy/aliasing distinction to force).
from enum import Enum


class Color(Enum):
    RED = 0
    GREEN = 1
    BLUE = 2


def describe(c: Color = Color.GREEN) -> Color:
    return c


class Painter:
    def paint(self, c: Color = Color.BLUE) -> Color:
        return c


def main() -> None:
    print(int(describe().value))           # 1 -- default GREEN
    print(int(describe(Color.RED).value))  # 0 -- explicit override

    p = Painter()
    print(int(p.paint().value))            # 2 -- default BLUE
    print(int(p.paint(Color.RED).value))   # 0 -- explicit override


main()
