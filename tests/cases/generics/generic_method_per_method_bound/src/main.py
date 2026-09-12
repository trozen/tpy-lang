# Per-method bound on class type parameter
# The class is generic on T, but is_sorted requires T: Comparable
from tpy import int32, Comparable

class Container[T]:
    items: list[T]

    def __init__(self):
        self.items = []

    def add(self, item: T) -> None:
        self.items.append(item)

    def is_sorted[T: Comparable](self) -> bool:
        i: int32 = int32(1)
        while i < int32(len(self.items)):
            if self.items[i] < self.items[i - int32(1)]:
                return False
            i = i + int32(1)
        return True

def main() -> None:
    c: Container[int32] = Container[int32]()
    c.add(int32(30))
    c.add(int32(10))
    c.add(int32(20))
    print(c.is_sorted())

    c2: Container[int32] = Container[int32]()
    c2.add(int32(1))
    c2.add(int32(2))
    c2.add(int32(3))
    print(c2.is_sorted())

main()
