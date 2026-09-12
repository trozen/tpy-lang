# Test that sort() on a type without __lt__ produces a clear error
from tpy import int32

class Foo:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

def main() -> None:
    a: list[Foo] = [Foo(3), Foo(1), Foo(2)]
    a.sort()  # tpyc: error(/sort.*requires.*Comparable.*Foo/)

main()
