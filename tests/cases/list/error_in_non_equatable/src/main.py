# Test that 'in' on a list of non-equatable type produces an error
from tpy import Int32

class Foo:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

def main() -> None:
    a: list[Foo] = [Foo(1), Foo(2)]
    result = Foo(1) in a  # tpyc: error(/Foo.*Equatable/)

main()
