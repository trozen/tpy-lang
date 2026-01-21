from tpy import Int32

class Counter:
    value: Int32

    def __init__(self):
        self.value = 0

    def increment(self) -> None:
        self.value = self.value + 1

    def add(self, n: Int32) -> None:
        self.value = self.value + n

    def get(self) -> Int32:
        return self.value

    def reset(self) -> None:
        self.value = 0

c = Counter()
print(c.get())
c.increment()
print(c.get())
c.add(5)
print(c.get())
c.reset()
print(c.get())
