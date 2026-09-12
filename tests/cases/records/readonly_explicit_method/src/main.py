# Explicit @readonly on methods generates const-qualified C++ overloads.
from tpy import int32, readonly

class Counter:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

    @readonly
    def get_value(self) -> int32:
        return self.value

    @readonly
    def doubled(self) -> int32:
        return self.value + self.value

def main() -> None:
    c: Counter = Counter(21)
    print(c.get_value())
    print(c.doubled())

main()
