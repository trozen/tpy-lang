# Test that inplace dunders must return self, not None
from tpy import int32

class Counter:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def __iadd__(self, other: Counter) -> None:  # tpyc: error(must return self)
        self.value += other.value

def main() -> None:
    c = Counter(int32(1))

main()
