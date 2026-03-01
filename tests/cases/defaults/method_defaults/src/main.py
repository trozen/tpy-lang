# Default parameter values for regular methods
from tpy import Int32

class Counter:
    count: Int32
    def __init__(self, start: Int32 = Int32(0)) -> None:
        self.count = start

    def increment(self, amount: Int32 = Int32(1)) -> None:
        self.count = self.count + amount

    def display(self, prefix: str = "count") -> None:
        print(f"{prefix}: {self.count}")

def main() -> None:
    c = Counter()
    c.display()
    c.increment()
    c.display()
    c.increment(Int32(5))
    c.display()
    c.display("total")

    c2 = Counter(Int32(100))
    c2.display()

main()
