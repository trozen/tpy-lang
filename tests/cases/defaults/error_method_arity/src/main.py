# Error: method arity mismatch with default params
from tpy import int32

class Counter:
    value: int32
    def __init__(self, value: int32 = int32(0)) -> None:
        self.value = value
    def increment(self, n: int32 = int32(1)) -> None:
        self.value = self.value + n

def main() -> None:
    c = Counter()
    c.increment(int32(1), int32(2))  # tpyc: error(/expects 0 to 1 arguments, got 2/)

main()
