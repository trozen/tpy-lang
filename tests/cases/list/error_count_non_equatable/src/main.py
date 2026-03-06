# Test that count() on a type without __eq__ produces an error
from tpy import Int32

class Foo:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

def main() -> None:
    a: list[Foo] = [Foo(1), Foo(2)]
    a.count(Foo(1))  # tpyc: error(/Equatable.*Foo/)

main()
