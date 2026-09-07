# A FIELD read of a value record at an `Optional[value record]` parameter:
# neither the constructor rvalue nor the bare name the argument rows admit.
# `h.m(w.v)` still rejects.
from tpy import Int32, ValueType


class Vec(ValueType):
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class W:
    v: Vec

    def __init__(self) -> None:
        self.v = Vec(3)


class H:
    def __init__(self) -> None:
        pass

    def m(self, v: Vec | None = None) -> Int32:
        if v is None:
            return -1
        return v.n


def f() -> None:
    h = H()
    w = W()
    # The argument is a field read at the optional value-record slot.
    print(h.m(w.v))  # tpyc: error(/stmt\.expr_stmt:method\.arg_shape/)


def main() -> None:
    f()


main()
