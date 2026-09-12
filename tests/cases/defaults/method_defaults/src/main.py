# Default parameter values for regular methods
from tpy import int32

class Counter:
    count: int32
    def __init__(self, start: int32 = int32(0)) -> None:
        self.count = start

    def increment(self, amount: int32 = int32(1)) -> None:
        self.count = self.count + amount

    def display(self, prefix: str = "count") -> None:
        print(f"{prefix}: {self.count}")

def main() -> None:
    c = Counter()
    c.display()
    c.increment()
    c.display()
    c.increment(int32(5))
    c.display()
    c.display("total")

    c2 = Counter(int32(100))
    c2.display()

main()
