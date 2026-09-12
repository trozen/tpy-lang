# @property with getter and setter, including validation in setter
from tpy import int32

class Clamped:
    _value: int32

    def __init__(self, value: int32) -> None:
        self._value = value

    @property
    def value(self) -> int32:
        return self._value

    @value.setter
    def value(self, v: int32) -> None:
        if v < 0:
            self._value = 0
        elif v > 100:
            self._value = 100
        else:
            self._value = v

def main() -> None:
    c = Clamped(50)
    print(c.value)
    c.value = 200
    print(c.value)
    c.value = -10
    print(c.value)
    c.value = 42
    print(c.value)

main()
