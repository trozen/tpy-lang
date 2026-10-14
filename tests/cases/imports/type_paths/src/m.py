# The classes and enums main reaches through `m.` / `mm.` / `MO.` paths.
from enum import Enum
from typing import Final, Protocol
from tpy import Own, ReturnException, int32


class Color(Enum):
    RED = 1
    BLUE = 2


class Outer:
    class Inner:
        K: Final[int32] = 9

        def __init__(self, v: int) -> None:
            self.v = v

    class Kind(Enum):
        A = 1
        B = 2

    class Err(Exception):
        pass

    LIMIT: Final[int32] = 7


class Box[T]:
    def __init__(self, v: Own[T]) -> None:
        self.v = v


def make_inner(v: int) -> Own[Outer.Inner]:
    i = Outer.Inner(v)
    return i


def box_of(v: int) -> Own[Box[int]]:
    b = Box(v)
    return b


def blue() -> Color:
    return Color.BLUE


class A:
    def __init__(self, v: int) -> None:
        self.v = v


class B:
    def __init__(self, v: int) -> None:
        self.v = v


class Err(Exception):
    def __init__(self, code: int) -> None:
        super().__init__("err")
        self.code = code


class RErr(Exception, ReturnException):
    pass


class HasV(Protocol):
    def get_v(self) -> int: ...
