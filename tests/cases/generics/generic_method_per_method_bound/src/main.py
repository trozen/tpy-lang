# Per-method bound on class type parameter
# The class is generic on T, but is_sorted requires T: Comparable
from tpy import Int32, Comparable

class Container[T]:
    items: list[T]

    def __init__(self):
        self.items = []

    def add(self, item: T) -> None:
        self.items.append(item)

    def is_sorted[T: Comparable](self) -> bool:
        i: Int32 = Int32(1)
        while i < Int32(len(self.items)):
            if self.items[i] < self.items[i - Int32(1)]:
                return False
            i = i + Int32(1)
        return True

def main() -> None:
    c: Container[Int32] = Container[Int32]()
    c.add(Int32(30))
    c.add(Int32(10))
    c.add(Int32(20))
    print(c.is_sorted())

    c2: Container[Int32] = Container[Int32]()
    c2.add(Int32(1))
    c2.add(Int32(2))
    c2.add(Int32(3))
    print(c2.is_sorted())

main()
