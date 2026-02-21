# Test __len__() fallback for truthiness when __bool__ is not defined
from tpy import Int32

class Stack:
    size: Int32

    def __init__(self, size: Int32) -> None:
        self.size = size

    def __len__(self) -> Int32:
        return self.size

def main() -> None:
    s1 = Stack(Int32(3))
    s2 = Stack(Int32(0))

    # bool() with __len__ fallback
    print(bool(s1))  # True
    print(bool(s2))  # False

    # if with __len__ fallback
    if s1:
        print("s1 truthy")
    if s2:
        print("s2 truthy")

    # not with __len__ fallback
    if not s2:
        print("s2 falsy")

main()
