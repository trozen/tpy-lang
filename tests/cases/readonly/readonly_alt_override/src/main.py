# @override + @readonly_alt: child overrides a @readonly_alt parent method.
# Both mutable and const clones must be generated and wired correctly in the child.
from tpy import Int32, Span, readonly, readonly_alt
from typing import override


class Base:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [Int32(1), Int32(2)]

    @readonly_alt
    def items(self) -> Span[readonly_alt[Int32]]:
        return self._data


class Child(Base):
    _extra: list[Int32]

    def __init__(self) -> None:
        super().__init__()
        self._extra = [Int32(3), Int32(4)]

    @override
    @readonly_alt
    def items(self) -> Span[readonly_alt[Int32]]:  # tpyc: warning(/non-polymorphic/)
        return self._extra


def read_base(b: readonly[Base]) -> None:
    s = b.items()  # tpyc: type(Span[readonly[Int32]])
    print(s[Int32(0)])


def read_child(c: readonly[Child]) -> None:
    s = c.items()  # tpyc: type(Span[readonly[Int32]])
    print(s[Int32(0)])
    print(s[Int32(1)])


def main() -> None:
    b = Base()
    s = b.items()  # tpyc: type(Span[Int32])
    print(s[Int32(0)])
    read_base(b)

    c = Child()
    s2 = c.items()  # tpyc: type(Span[Int32])
    print(s2[Int32(0)])
    read_child(c)


main()
