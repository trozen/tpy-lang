from typing import Protocol
from tpy import Int32, Own

# User-defined protocol
class Printable(Protocol):
    def to_str(self) -> str: ...

# Implementation of Printable
class Message:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text

    def to_str(self) -> str:
        return self.text

# Record with user-defined protocol bound
class Container[T: Printable]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def print_value(self) -> None:
        print(self.value.to_str())

# Protocol that references the bounded record - this is the key test
class ContainerFactory(Protocol):
    def make(self, text: str) -> Own[Container[Message]]: ...

# Factory implementation
class DefaultFactory:
    def make(self, text: str) -> Own[Container[Message]]:
        return Container(Message(text))

# Generic function using the factory protocol
def create_container[F: ContainerFactory](factory: F, text: str) -> Own[Container[Message]]:
    return factory.make(text)

def main() -> None:
    factory = DefaultFactory()
    container = create_container(factory, "Hello from container")
    container.print_value()

main()
