from typing import Protocol
from tpy import Int32, Own

# A record that will be referenced by a bound protocol
class Foo:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

# User-defined protocol that references Foo
class FooMaker(Protocol):
    def make(self) -> Own[Foo]: ...

# Implementation of FooMaker
class DefaultFooMaker:
    def make(self) -> Own[Foo]:
        return Foo(Int32(42))

# Record with the bound protocol as a type parameter bound
class Bar[T: FooMaker]:
    factory: T

    def __init__(self, factory: T) -> None:
        self.factory = factory

    def create_foo(self) -> Own[Foo]:
        return self.factory.make()

# Protocol that references the bounded record Bar
class BarUser(Protocol):
    def use_bar(self, bar: Bar[DefaultFooMaker]) -> Int32: ...

def main() -> None:
    factory = DefaultFooMaker()
    bar = Bar(factory)
    foo = bar.create_foo()
    print(foo.value)

main()
