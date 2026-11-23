# A generic stub's result at a class instantiation may be its argument, as
# its TPy twin's `return n` may: returning it over a local is refused.
from tpy import int32
from tpy.extern import cpp_template


class Node:
    def __init__(self, v: int32) -> None:
        self.v = v


@cpp_template("::identity({0})")
def same[T](n: T) -> T: ...


def escape() -> Node:
    x = Node(9)
    return same(x)  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(escape().v)


main()
