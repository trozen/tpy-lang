# Error: tuple is not Comparable when an element type lacks Comparable.
# Parallel to error_tuple_order_no_lt (which checks the direct `<` operator
# path); this case validates the protocol-conformance path through a
# generic T: Comparable bound.
from tpy import Comparable, Int32

class Foo:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v

def less[T: Comparable](a: T, b: T) -> bool:
    return a < b

def main() -> None:
    a = (Int32(1), Foo(1))
    b = (Int32(2), Foo(2))
    print(less[tuple[Int32, Foo]](a, b))  # tpyc: error(/does not satisfy bound 'Comparable'/)

main()
