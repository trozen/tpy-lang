# `cls` is a name for the class, not a value: it cannot be bound to a variable
# (that needs `type[T]`, which is not supported yet).
from tpy import int32


class P:
    def __init__(self, x: int32):
        self.x = x

    @classmethod
    def leak(cls) -> int32:
        c = cls  # tpyc: error(/'cls' can only be used to construct/)
        return 1


def main() -> None:
    print(P.leak())


main()
