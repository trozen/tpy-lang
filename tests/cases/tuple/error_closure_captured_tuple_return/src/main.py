# A closure-captured mixed tuple returned whole is refused, as its concrete
# twin is (BUGS.md#closure-captured-mixed-tuple-return).
from tpy import Copyable, Own, int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


def closure[T: Copyable](x: tuple[Own[T], A]) -> tuple[Own[T], A]:
    def g() -> None:
        print(x)
    g()
    return x  # tpyc: warning(/may copy tuple\[Own\[T\], A\] into owned storage/) error(/return.tuple_source/)


def main() -> None:
    print("never built")


main()
