# A call macro expanding to a constructor call as a bare statement runs the
# constructor on its REAL argument (`T(name);` in C++ would declare `name`).
from tpy import int32
from ctor_macros import build


class Tick:
    def __init__(self, n: int32 = 0) -> None:
        self.n = n
        print("tick", n)


def run(flag: bool) -> None:
    k: int32 = 3
    # the name's own block: a redeclaration, were it a declaration
    build(k)  # tpyc: ok
    if flag:
        # a nested block: a declaration here would shadow `k`
        build(k)  # tpyc: ok
    print("after", k)


run(True)
