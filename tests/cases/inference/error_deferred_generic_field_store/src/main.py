# A field typed by an inferred type argument refuses a wider store:
# docs/LANGUAGE_FEATURES.md "Implicit narrowing".
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
    c.val = int64(2)  # tpyc: error(/field 'val': expected int32, got int64; the type argument of 'c' was inferred/)


main()
