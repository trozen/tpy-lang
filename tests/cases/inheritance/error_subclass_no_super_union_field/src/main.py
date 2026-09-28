# No super() over an `__init__`-less base whose union field's first alternative
# has no default ctor (LANGUAGE_FEATURES "Single class inheritance").
from tpy import int32


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __del__(self) -> None:
        pass


class B:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class Base:
    val: A | B


class Child(Base):
    extra: int32

    def __init__(self, e: int32) -> None:   # tpyc: error(/cannot be constructed without arguments \(field 'val' .*its first alternative 'A'/)
        self.val = B(e)
        self.extra = e


def main() -> None:
    c = Child(7)
    print(c.extra)


main()
