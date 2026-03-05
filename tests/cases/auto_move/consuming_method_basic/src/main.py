# Test consuming methods (self: Own[Self]) -- basic happy path.
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
    result = w.take()
    print(result)

    # Calling on a temporary should also work
    print(Wrapper(99).take())

main()
