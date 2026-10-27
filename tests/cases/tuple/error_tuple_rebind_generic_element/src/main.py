# A plain function's rebound tuple owning a generic-T element beside a
# borrowed one is refused (BUGS.md#rebound-tuple-generic-element-rejects).
from tpy import Own, int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


def mk[T](x: Own[T], b: A) -> tuple[Own[T], A]:
    return (x, b)


def rebind[T](x: Own[T], y: Own[T], b: A) -> int32:
    t = mk(x, b)  # tpyc: error(/rebinding 't' while it owns element 0, a generic type parameter, is not yet supported here/)
    print("first", t[1].v)
    t = mk(y, b)
    return t[1].v


def main() -> None:
    print(rebind(A(1), A(2), A(0)))


main()
