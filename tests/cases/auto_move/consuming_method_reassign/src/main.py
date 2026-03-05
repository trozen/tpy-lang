# Test that reassigning a consumed variable revives it.
from typing import Self
from tpy import Own

class Wrapper:
    _value: int

    def __init__(self, value: int):
        self._value = value

    def take(self: Own[Self]) -> int:
        return self._value

def main() -> None:
    w = Wrapper(42)
    r1 = w.take()
    w = Wrapper(99)
    r2 = w.take()
    print(r1)
    print(r2)

main()
