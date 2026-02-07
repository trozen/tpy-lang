"""Test @staticmethod decorator on class methods."""
from tpy import Int32

class Counter:
    value: Int32

    def __init__(self, start: Int32):
        self.value = start

    @staticmethod
    def zero() -> Int32:
        return 0

    @staticmethod
    def add(a: Int32, b: Int32) -> Int32:
        return a + b

    def get(self) -> Int32:
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
