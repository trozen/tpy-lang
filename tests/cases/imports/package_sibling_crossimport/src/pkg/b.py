from enum import Enum
from typing import Final, Protocol
from tpy import Int32, dynamic

V: Final[Int32] = 3


class K(Enum):
    ONE = 1
    TWO = 2


# @dynamic protocol re-export: covers the descendant-guard skip for
# protocols (mirrors function/variable suppression -- the using-decl
# in pkg.hpp would otherwise hit the same parent<->sub include cycle).
@dynamic
class Greeter(Protocol):
    def greet(self) -> Int32:
        ...


class B:
    x: Int32

    def __init__(self) -> None:
        self.x = Int32(7)

    def greet(self) -> Int32:
        # Conforms to Greeter via structural matching.
        return self.x

    def value(self) -> Int32:
        return self.x


def g(n: Int32) -> Int32:
    return n + Int32(1)
