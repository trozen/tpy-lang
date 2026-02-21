# @dynamic protocol with methods only from parent -- not a marker protocol
from tpy import dynamic
from typing import Protocol

class HasName(Protocol):
    def name(self) -> str: ...

@dynamic
class DynNamed(HasName, Protocol):
    pass

class Dog(DynNamed):
    def name(self) -> str:
        return "Rex"

class Cat:
    def name(self) -> str:
        return "Whiskers"

def greet(n: DynNamed) -> None:
    print(n.name())

def main() -> None:
    greet(Dog())
    greet(Cat())

main()
