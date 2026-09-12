# First declared parent has no __init__; super().__init__() resolves past it
# to the second base (HasInit) which is the MRO-first __init__-base. The
# coverage validator accepts this single super() call (HasInit is the only
# base with __init__).
from tpy import int32


class NoInitMixin:
    pass


class HasInit:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class C(NoInitMixin, HasInit):
    def __init__(self, x: int32) -> None:
        super().__init__(x)


def main() -> None:
    c = C(int32(7))
    print(c.x)


main()
