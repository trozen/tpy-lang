# Test that calling a consuming method through a pointer is an error.
from typing import Self
from tpy import Own, Ptr

class Wrapper:
    _value: int

    def __init__(self, value: int):
        self._value = value

    def take(self: Own[Self]) -> int:
        return self._value

def main() -> None:
    w = Wrapper(42)
    p: Ptr[Wrapper] = w
    result = p.take()  # tpyc: error(/Deref chain/)
    print(result)

main()
