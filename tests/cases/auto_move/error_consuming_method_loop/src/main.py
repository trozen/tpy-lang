# Test that consuming an outer variable inside a loop is an error.
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
    for i in range(3):
        result = w.take()  # tpyc: error(/inside a loop/)
        print(result)

main()
