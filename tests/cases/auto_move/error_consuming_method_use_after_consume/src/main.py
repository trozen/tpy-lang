# Test that using a variable after a consuming method call is an error.
from typing import Self
from tpy import Own

class Wrapper:
    _value: int

    def __init__(self, value: int):
        self._value = value

    def take(self: Own[Self]) -> int:
        return self._value

    def get(self) -> int:
        return self._value

def main() -> None:
    w = Wrapper(42)
    result = w.take()
    print(w.get())  # tpyc: error(/consumed/)

main()
