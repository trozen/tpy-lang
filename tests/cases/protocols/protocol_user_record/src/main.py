from typing import Sized
from tpy import Int32

class MyContainer:
    size: Int32

    def __init__(self, size: Int32) -> None:
        self.size = size

    def __len__(self) -> Int32:
        return self.size

def count(items: Sized) -> Int32:
    return len(items)

def main() -> None:
    c: MyContainer = MyContainer(42)
    print(count(c))

main()
