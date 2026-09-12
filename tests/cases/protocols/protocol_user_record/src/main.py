from typing import Sized
from tpy import int32

class MyContainer:
    size: int32

    def __init__(self, size: int32) -> None:
        self.size = size

    def __len__(self) -> int32:
        return self.size

def count(items: Sized) -> int32:
    return len(items)

def main() -> None:
    c: MyContainer = MyContainer(42)
    print(count(c))

main()
