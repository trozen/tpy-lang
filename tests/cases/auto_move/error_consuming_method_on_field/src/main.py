# Test that calling a consuming method on a field is an error.
from typing import Self
from tpy import Own

class Inner:
    _value: int

    def __init__(self, value: int):
        self._value = value

    def take(self: Own[Self]) -> int:
        return self._value

class Outer:
    inner: Inner

    def __init__(self, inner: Inner):
        self.inner = inner

    def try_take(self) -> int:
        return self.inner.take()  # tpyc: error(/consuming.*field/)

def main() -> None:
    o = Outer(Inner(42))
    print(o.try_take())

main()
