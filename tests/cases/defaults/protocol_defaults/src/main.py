# Protocol conformance when implementation has default params
from typing import Protocol
from tpy import Int32

class Callable(Protocol):
    def call(self) -> Int32: ...

class OneArg(Protocol):
    def process(self, x: Int32) -> Int32: ...

class Impl:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value
    def call(self, extra: Int32 = Int32(0)) -> Int32:
        return self.value + extra
    def process(self, x: Int32, scale: Int32 = Int32(1)) -> Int32:
        return x * scale + self.value

def use_callable(c: Callable) -> None:
    print(c.call())

def use_one_arg(p: OneArg) -> None:
    print(p.process(Int32(10)))

def main() -> None:
    impl = Impl(Int32(42))
    use_callable(impl)
    use_one_arg(impl)

main()
