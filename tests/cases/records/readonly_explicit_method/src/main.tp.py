# Explicit @readonly on methods generates const-qualified C++ overloads.
from tpy import Int32, readonly

class Counter:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

    @readonly
    def get_value(self) -> Int32:
        return self.value

    @readonly
    def doubled(self) -> Int32:
        return self.value + self.value

def main() -> None:
    c: Counter = Counter(21)
    print(c.get_value())
    print(c.doubled())

main()
