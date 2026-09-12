from typing import Protocol
from tpy import int32, Own

# User-defined protocol that will be a bound on Wrapper
class Printable(Protocol):
    def to_str(self) -> str: ...

# Record that implements Printable
class Message:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text

    def to_str(self) -> str:
        return self.text

# Record with user-defined bound, referenced by a bound protocol
class Wrapper[T: Printable]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def print_wrapped(self) -> None:
        print(self.value.to_str())

# Bound protocol that references Wrapper[Message]
class WrapperMaker(Protocol):
    def make(self, text: str) -> Own[Wrapper[Message]]: ...

# Implementation of WrapperMaker
class DefaultWrapperMaker:
    def make(self, text: str) -> Own[Wrapper[Message]]:
        return Wrapper(Message(text))

# Record that uses WrapperMaker as a bound
class Container[T: WrapperMaker]:
    factory: T

    def __init__(self, factory: T) -> None:
        self.factory = factory

    def create_wrapper(self, text: str) -> Own[Wrapper[Message]]:
        return self.factory.make(text)

def main() -> None:
    factory = DefaultWrapperMaker()
    container = Container(factory)
    wrapper = container.create_wrapper("Hello from wrapper")
    wrapper.print_wrapped()

main()
