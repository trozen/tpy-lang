"""Test @staticmethod decorator on class methods."""
from tpy import int32

class Counter:
    value: int32

    def __init__(self, start: int32):
        self.value = start

    @staticmethod
    def zero() -> int32:
        return 0

    @staticmethod
    def add(a: int32, b: int32) -> int32:
        return a + b

    def get(self) -> int32:
        return self.value

# Call static method via class name
print(Counter.zero())
print(Counter.add(10, 20))

# Call static method via instance (also valid)
c = Counter(100)
print(c.zero())
print(c.add(3, 4))

# Regular instance method still works
print(c.get())
