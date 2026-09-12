# Test that remove() on a type without __eq__ produces an error
from tpy import int32

class Foo:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

def main() -> None:
    a: list[Foo] = [Foo(1), Foo(2)]
    a.remove(Foo(1))  # tpyc: error(/Equatable.*Foo/)

main()
