# Test Truthy protocol as a type bound for generic functions
from tpy import Truthy

class Box:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value

    def __bool__(self) -> bool:
        return self.value != 0

def check[T: Truthy](x: T) -> bool:
    return bool(x)

def main() -> None:
    b1 = Box(42)
    b2 = Box(0)
    print(check(b1))  # True
    print(check(b2))  # False

main()
