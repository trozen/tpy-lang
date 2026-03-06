# Test that a class with __len__() -> int conforms to Sized protocol.
from typing import Sized

class MyCollection:
    size: int

    def __init__(self, n: int) -> None:
        self.size = n

    def __len__(self) -> int:
        return self.size

def count(items: Sized) -> int:
    return len(items)

def main() -> None:
    c = MyCollection(42)
    print(count(c))

main()
