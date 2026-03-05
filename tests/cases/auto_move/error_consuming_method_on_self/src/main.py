# Test that calling a consuming method on self is an error.
from typing import Self
from tpy import Own

class Builder:
    _value: int

    def __init__(self, value: int):
        self._value = value

    def take(self: Own[Self]) -> int:
        return self._value

    def try_take(self) -> int:
        return self.take()  # tpyc: error(/consuming.*self/)

def main() -> None:
    b = Builder(42)
    print(b.try_take())

main()
