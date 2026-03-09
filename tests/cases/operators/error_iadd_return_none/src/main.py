# Test that inplace dunders must return self, not None
from tpy import Int32

class Counter:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def __iadd__(self, other: Counter) -> None:  # tpyc: error(must return self)
        self.value += other.value

def main() -> None:
    c = Counter(Int32(1))

main()
