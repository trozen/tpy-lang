# A `@dynamic` accessor result is the stored object, so an `Own` argument
# cannot take it (LANGUAGE_FEATURES "Owning and non-owning locals": only the
# original owner can be moved at its last use).
from typing import Protocol
from tpy import int32, dynamic, Own
from tplib import Box


@dynamic
class Pet(Protocol):
    def val(self) -> int32: ...


class Dog:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def val(self) -> int32:
        return self.n


def take(p: Own[Pet]) -> Own[Pet]:
    return p


def main() -> None:
    b: Box[Pet] = Box(Dog(1))
    print(take(b.get()).val())  # tpyc: error(/non-copyable type 'Pet'/)


main()
