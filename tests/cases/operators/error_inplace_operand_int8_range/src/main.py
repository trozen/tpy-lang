# An int literal no `int8` holds at an in-place dunder's `int8` parameter is
# refused, as the explicit `v.__iadd__(300)` call refuses it.
from tpy import int8, int32


class Small:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __iadd__(self, k: int8) -> "Small":
        self.n += k
        return self


def main() -> None:
    v = Small(1)
    v += 300  # tpyc: error(/Integer literal 300 is outside int8 range/)
    print(v.n)


main()
