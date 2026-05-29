# isinstance through a @dynamic protocol borrow requires the check type to
# C++-inherit the protocol. A structural conformer (Cat has name() but does
# not inherit Pet) can never sit behind a Pet& borrow, so the dynamic_cast
# would always be False -- reject it rather than silently folding.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class Cat:                       # structural conformer, does NOT inherit Pet
    def name(self) -> str:
        return "cat"


def describe(p: Pet) -> str:
    if isinstance(p, Cat):       # tpyc: error(/do not inherit the @dynamic protocol 'Pet'/)
        return "cat"
    return p.name()


def main() -> None:
    print(describe(Dog()))


main()
