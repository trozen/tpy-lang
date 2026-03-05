# Test that calling a consuming method through a Deref chain is an error.
from typing import Self
from tpy import Own, Deref

class Inner:
    _value: int

    def __init__(self, value: int):
        self._value = value

    def take(self: Own[Self]) -> int:
        return self._value

class Outer(Deref[Inner]):
    _inner: Inner

    def __init__(self, value: int):
        self._inner = Inner(value)

    def __deref__(self) -> Inner:
        return self._inner

def main() -> None:
    o = Outer(42)
    result = o.take()  # tpyc: error(/Deref chain/)
    print(result)

main()
