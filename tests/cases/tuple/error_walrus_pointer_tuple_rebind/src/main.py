# A walrus rebinding a pointer tuple whose `=` write is several statements
# is refused (BUGS.md#walrus-pointer-tuple-rebind-rejects).
from tpy import int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def f() -> None:
    t = (1, Box(1))
    t = (2, Box(2))
    # The rebind to a fresh tuple holding a record is the subject.
    print((t := (3, Box(4)))[0])  # tpyc: error(/this construct is not yet supported by C\+\+ code generation \(expr.walrus\)/)
    print(t[1].n)


f()
