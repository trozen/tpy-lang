from typing import Protocol
from tpy import Int32, Own

# A record that will be referenced by a prereq protocol
class Result:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

# Prereq protocol that references Result in its signature
class Convertible(Protocol):
    def to_result(self) -> Own[Result]: ...

# Record that implements Convertible
class Message:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text

    def to_result(self) -> Own[Result]:
        return Result(Int32(42))

# Record with prereq protocol as bound, referenced by a bound protocol
class Wrapper[T: Convertible]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get_result(self) -> Own[Result]:
        return self.value.to_result()

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
    wrapper = container.create_wrapper("test")
    result = wrapper.get_result()
    print(result.value)

main()
