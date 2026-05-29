# isinstance against a check type that neither inherits nor structurally
# conforms to the @dynamic protocol can never match (it can't sit behind the
# Pet* borrow, even via an adapter), so it is rejected rather than folding to
# a silent False.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class Rock:                      # no name() -- does not conform to Pet at all
    def weight(self) -> int:
        return 5


def describe(p: Pet) -> str:
    if isinstance(p, Rock):      # tpyc: error(/do not conform to the @dynamic protocol 'Pet'/)
        return "rock"
    return p.name()


def main() -> None:
    print(describe(Dog()))


main()
