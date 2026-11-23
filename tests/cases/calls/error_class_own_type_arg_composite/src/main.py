# Error: a member holding the class's `T` inside a tuple refuses `C[Own[P]]`
# where it is formed (BUGS.md#own-type-arg-composite-leaf-refused).
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Tagged[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    def tagged(self) -> tuple[T, int32]:
        return (self.v, 1)


def main() -> None:
    t = Tagged[Own[P]](P(1))  # tpyc: error(/`Own\[P\]` as a type argument of class 'Tagged' is not supported yet: 'T' appears inside 'tuple\[T, int32\]' in 'Tagged.tagged'.*write the class with `Own\[P\]` in place of 'T'/)
    print(t.v.n)


main()
