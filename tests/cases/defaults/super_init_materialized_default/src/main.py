# A base __init__ arg omitted by super().__init__ renders in the member
# initializer list, which needs the parameter's target type to spell a
# materialized default by its slot's shape rather than as a null pointer.
from tpy import Int64, ValueType


class Fixed(ValueType):
    off: Int64

    def __init__(self, off: Int64) -> None:
        self.off = off


class Base:
    v: Int64

    def __init__(self, a: Int64, tz: "Fixed | None" = None, *, tag: Int64) -> None:
        self.v = a * 100 + (0 if tz is None else tz.off) * 10 + tag


class Sub(Base):
    def __init__(self, a: Int64) -> None:
        super().__init__(a, tag=1)


class Passing(Base):
    def __init__(self, a: Int64) -> None:
        super().__init__(a, Fixed(5), tag=2)


def main() -> None:
    print(Base(1, tag=3).v)
    print(Sub(4).v)
    print(Passing(6).v)


main()
