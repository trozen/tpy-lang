# An enum value on the left of `in` / `not in`: every membership arm takes it
# as a by-value needle, compared by `==`, as CPython does.
from enum import Enum, IntEnum
from typing import Iterator
from tpy import int32
from shades import Shade


class Color(Enum):
    Red = 1
    Green = 2
    Blue = 3


class Prio(IntEnum):
    Low = 1
    High = 2


class Outer:
    class Kind(Enum):
        A = 1
        B = 2


class Palette:
    colors: list[Color]
    main: Color

    def __init__(self) -> None:
        self.colors = [Color.Red, Color.Blue]
        self.main = Color.Blue

    def has(self, c: Color) -> bool:
        # method body: a field haystack
        return c in self.colors  # tpyc: ok


class Bag:
    def __contains__(self, c: Color) -> bool:
        return c == Color.Green


class ColorIter:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> 'ColorIter':
        return self

    def __next__(self) -> Color:
        self.i += 1
        if self.i == 1:
            return Color.Red
        if self.i == 2:
            return Color.Green
        raise StopIteration


def get_color() -> Color:
    return Color.Green


def gen(cs: list[Color]) -> Iterator[bool]:
    for c in [Color.Red, Color.Green]:
        # generator body
        yield c in cs  # tpyc: ok


def main() -> None:
    xs = [Color.Red]
    print("list_local", Color.Red in xs, Color.Green in xs)  # tpyc: ok
    print("list_literal", Color.Red in [Color.Red, Color.Blue])  # tpyc: ok
    p = Palette()
    print("list_field", Color.Blue in p.colors, Color.Green in p.colors)  # tpyc: ok
    s = {Color.Green}
    print("set", Color.Red in s, Color.Green in s)  # tpyc: ok
    d = {Color.Red: 1}
    print("dict", Color.Red in d, Color.Blue in d)  # tpyc: ok
    print("dict_keys", Color.Red in d.keys())  # tpyc: ok
    c = Color.Blue
    print("tuple_name", c in (Color.Red, Color.Green))  # tpyc: ok
    print("tuple_member", Color.Green in (Color.Red, Color.Green))  # tpyc: ok
    b = Bag()
    print("user_contains", Color.Green in b, Color.Red in b)  # tpyc: ok
    it = ColorIter()
    print("user_iter", Color.Green in it)  # tpyc: ok
    print("not_in", Color.Green not in xs, Color.Red not in xs)  # tpyc: ok
    print("intenum", Prio.Low in [Prio.Low, Prio.High])  # tpyc: ok
    print("call_list", get_color() in xs)  # tpyc: ok
    print("call_tuple", get_color() in (Color.Red, Color.Green))  # tpyc: ok
    print("field_needle", p.main in p.colors)  # tpyc: ok
    shs = [Shade.Dark]
    print("imported", Shade.Dark in shs, Shade.Light in shs)  # tpyc: ok
    ks = [Outer.Kind.B]
    print("nested", Outer.Kind.B in ks, Outer.Kind.A in ks)  # tpyc: ok
    print("comprehension", [k in xs for k in [Color.Red, Color.Green]])  # tpyc: ok
    print("generator", list(gen(xs)))
    print("method", p.has(Color.Red), p.has(Color.Green))
    # Inverse: the other enum operators keep their rules.
    print("eq", Color.Red == Color.Red, Color.Red != Color.Green)  # tpyc: ok
    print("intenum_lt", Prio.Low < Prio.High)  # tpyc: ok


main()
