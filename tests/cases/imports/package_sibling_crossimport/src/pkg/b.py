from enum import Enum
from typing import Final, Protocol
from tpy import int32, dynamic

V: Final[int32] = 3


class K(Enum):
    ONE = 1
    TWO = 2


# @dynamic protocol re-export: covers the descendant-guard skip for
# protocols (mirrors function/variable suppression -- the using-decl
# in pkg.hpp would otherwise hit the same parent<->sub include cycle).
@dynamic
class Greeter(Protocol):
    def greet(self) -> int32:
        ...


class B:
    x: int32

    def __init__(self) -> None:
        self.x = int32(7)

    def greet(self) -> int32:
        # Conforms to Greeter via structural matching.
        return self.x

    def value(self) -> int32:
        return self.x


def g(n: int32) -> int32:
    return n + int32(1)
