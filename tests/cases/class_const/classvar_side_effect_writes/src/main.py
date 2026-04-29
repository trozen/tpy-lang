# When a ClassVar write target has a side-effecting receiver (function call,
# subscript), codegen evaluates the receiver exactly once before emitting the
# qualified `<owner>::<member>` lvalue -- no GCC stmt-expr wrap on the LHS.
from typing import ClassVar
from tpy import Int32, Own


class Counter:
    instances: ClassVar[Int32] = 0

    def __init__(self) -> None:
        pass


def make_c() -> Own[Counter]:
    print("side_effect")
    return Counter()


def main() -> None:
    Counter.instances = 0
    make_c().instances = 5  # tpyc: warning(/Assigning to ClassVar 'Counter.instances' via instance/)
    print(Counter.instances)

    cs: list[Counter] = [Counter(), Counter()]
    cs[0].instances += 3  # tpyc: warning(/Assigning to ClassVar 'Counter.instances' via instance/)
    print(Counter.instances)


main()
