# A `case ... if <guard>:` guard is a boolean context, so a non-bool guard
# needs the same truthiness lowering an `if` gets. Guards used to render
# the value raw: a str/list guard did not compile, and a value-repr
# `Optional[scalar]` guard tested engagement only, so `Some(0)` matched.
from enum import Enum, IntEnum, auto
from typing import Any
from tpy import Int32


class Color(Enum):
    RED = auto()
    BLUE = auto()


class Level(IntEnum):
    ZERO = 0
    HIGH = 5


class Plain:
    # Neither __bool__ nor __len__ -- Python's default object truthiness.
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Bag:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __len__(self) -> Int32:
        return self.n


def opt_guard(k: Int32, v: Int32 | None) -> Int32:
    match k:
        # No optional-truthiness warning fires here, unlike the same test in
        # an `if` -- a diagnostic gap tracked in BUGS.md, not a fix target.
        case 1 if v:  # tpyc: ok
            return 10
        case _:
            return 20


def str_guard(k: Int32, t: str) -> Int32:
    match k:
        case 1 if t:
            return 10
        case _:
            return 20


def list_guard(k: Int32, xs: list[Int32]) -> Int32:
    match k:
        case 1 if xs:
            return 10
        case _:
            return 20


def record_guard(k: Int32, g: Bag) -> Int32:
    match k:
        case 1 if g:
            return 10
        case _:
            return 20


def not_guard(k: Int32, t: str) -> Int32:
    match k:
        case 1 if not t:
            return 10
        case _:
            return 20


def enum_guard(k: Int32, c: Color) -> Int32:
    match k:
        case 1 if c:
            return 10
        case _:
            return 20


def int_enum_guard(k: Int32, lv: Level) -> Int32:
    match k:
        case 1 if lv:
            return 10
        case _:
            return 20


def plain_record_guard(k: Int32, p: Plain) -> Int32:
    match k:
        case 1 if p:
            return 10
        case _:
            return 20


def any_guard(k: Int32, v: Any) -> Int32:
    match k:
        case 1 if v:
            return 10
        case _:
            return 20


def and_guard(k: Int32, t: str, xs: list[Int32]) -> Int32:
    match k:
        case 1 if t and xs:
            return 10
        case _:
            return 20


def main() -> None:
    print("opt", opt_guard(1, 0), opt_guard(1, 5), opt_guard(1, None))
    print("str", str_guard(1, ""), str_guard(1, "a"))
    print("list", list_guard(1, []), list_guard(1, [1]))
    print("record", record_guard(1, Bag(0)), record_guard(1, Bag(2)))
    # Enum members are always truthy; an IntEnum tests its value, so ZERO
    # is falsy. A dunder-less record is always truthy (Python default).
    print("enum", enum_guard(1, Color.RED), enum_guard(1, Color.BLUE))
    print("intenum", int_enum_guard(1, Level.ZERO), int_enum_guard(1, Level.HIGH))
    print("plain", plain_record_guard(1, Plain(0)))
    print("any", any_guard(1, 0), any_guard(1, 1))
    print("not", not_guard(1, ""), not_guard(1, "a"))
    print("and", and_guard(1, "", [1]), and_guard(1, "a", []),
          and_guard(1, "a", [1]))
    # The unguarded arm still wins when the subject does not match.
    print("miss", opt_guard(9, 5), str_guard(9, "a"))


main()
