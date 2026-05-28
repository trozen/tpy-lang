# Rvalue args (constructor calls, expressions) into a mutable reference-element
# *args slot. The indirect-mode pack stores T* and needs lvalue sources, so
# codegen hoists each non-lvalue arg into a typed temp before address-taking
# -- previously `&Box(1)` was emitted directly and failed C++ with "taking
# address of rvalue".
from tpy import Int32, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def sum_all(*items: Box) -> Int32:
    n: Int32 = 0
    for b in items:
        n += b.val
    return n


def main() -> None:
    print(sum_all(Box(3), Box(4)))  # tpyc: ok


main()
