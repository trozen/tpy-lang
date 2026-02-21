# Non-@dynamic protocol cannot be used as variable type
from typing import Protocol

class Greetable(Protocol):
    def greet(self) -> str:
        ...

class Person(Greetable):
    def greet(self) -> str:
        return "Hello"

def main() -> None:
    g: Greetable = Person()  # tpyc: error(/Only @dynamic protocols/)

main()
