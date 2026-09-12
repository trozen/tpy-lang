# Callable class with mutable state (mutating __call__)
from tpy import int32

class Counter:
    count: int32
    def __init__(self):
        self.count = 0
    def __call__(self, inc: int32) -> int32:
        self.count += inc
        return self.count

class Accumulator:
    total: float
    def __init__(self, initial: float):
        self.total = initial
    def __call__(self, value: float) -> float:
        self.total += value
        return self.total

def main():
    c = Counter()
    print(c(1))
    print(c(5))
    print(c(10))

    a = Accumulator(0.0)
    print(a(1.5))
    print(a(2.5))
    print(a(6.0))

main()
