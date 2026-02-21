from tpy import Int32

class Counter:
    value: Int32

    def __init__(self, start: Int32):
        self.value = start

    def increment(self) -> None:
        self.value = self.value + 1

    def add(self, n: Int32) -> None:
        self.value = self.value + n

    def get(self) -> Int32:
        return self.value

    def reset(self) -> None:
        self.value = 0

# Test __init__ with parameter
c = Counter(100)
print(c.get())

# Test methods
c.increment()
print(c.get())

c.add(5)
print(c.get())

c.reset()
print(c.get())

# Test multiple print arguments
a: Int32 = 42
b: Int32 = 99
print(a, b)

# Test string printing
print("done")
