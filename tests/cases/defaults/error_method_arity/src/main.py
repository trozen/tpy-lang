# Error: method arity mismatch with default params
from tpy import Int32

class Counter:
    value: Int32
    def __init__(self, value: Int32 = Int32(0)) -> None:
        self.value = value
    def increment(self, n: Int32 = Int32(1)) -> None:
        self.value = self.value + n

def main() -> None:
    c = Counter()
    c.increment(Int32(1), Int32(2))  # tpyc: error(/expects 0 to 1 arguments, got 2/)

main()
