# An argument whose type its function decides later is refused once wider than
# the inferred type argument: docs/LANGUAGE_FEATURES.md "Implicit narrowing".
from tpy import int32, int64


class Container[T]:
    val: T

    def __init__(self) -> None:
        pass

    def set(self, val: T) -> None:
        self.val = val


def main() -> None:
    c = Container()
    c.set(int32(1))
    n = int32(2)
    c.set(n)  # tpyc: error(/expected int32, got int64; the type argument of 'c' was inferred/)
    n = int64(5000000000)


main()
