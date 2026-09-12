# super().__init__() in a multi-base child where the MRO-first __init__ base
# is a generic instantiation. T -> int32 substitution flows through the
# super() call.
from tpy import int32


class Box[T]:
    val: T

    def __init__(self, v: T) -> None:
        self.val = v


class Logger:
    def log(self, msg: str) -> None:
        print("[log] " + msg)


class IntBox(Box[int32], Logger):
    def __init__(self, v: int32) -> None:
        super().__init__(v)


def main() -> None:
    ib = IntBox(int32(42))
    print(ib.val)


main()
