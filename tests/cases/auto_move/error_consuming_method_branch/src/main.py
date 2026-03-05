# Test that using a variable after consuming in one branch is an error.
from typing import Self
from tpy import Own, readonly

class Wrapper:
    _value: int

    def __init__(self, value: int):
        self._value = value

    def take(self: Own[Self]) -> int:
        return self._value

    @readonly
    def get(self) -> int:
        return self._value

def test_branch(flag: bool) -> None:
    w = Wrapper(42)
    if flag:
        result = w.take()
        print(result)
    print(w.get())  # tpyc: error(/consumed/)

test_branch(True)
