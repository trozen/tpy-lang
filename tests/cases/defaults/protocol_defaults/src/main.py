# Protocol conformance when implementation has default params
from typing import Protocol
from tpy import int32

class Callable(Protocol):
    def call(self) -> int32: ...

class OneArg(Protocol):
    def process(self, x: int32) -> int32: ...

class Impl:
    value: int32
    def __init__(self, value: int32) -> None:
        self.value = value
    def call(self, extra: int32 = int32(0)) -> int32:
        return self.value + extra
    def process(self, x: int32, scale: int32 = int32(1)) -> int32:
        return x * scale + self.value

def use_callable(c: Callable) -> None:
    print(c.call())

def use_one_arg(p: OneArg) -> None:
    print(p.process(int32(10)))

def main() -> None:
    impl = Impl(int32(42))
    use_callable(impl)
    use_one_arg(impl)

main()
