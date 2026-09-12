# @override + @auto_readonly: child overrides a @auto_readonly parent method.
# Both mutable and const clones must be generated and wired correctly in the child.
from tpy import int32, Span, readonly, auto_readonly
from typing import override


class Base:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [int32(1), int32(2)]

    @auto_readonly
    def items(self) -> Span[auto_readonly[int32]]:
        return self._data


class Child(Base):
    _extra: list[int32]

    def __init__(self) -> None:
        super().__init__()
        self._extra = [int32(3), int32(4)]

    @override
    @auto_readonly
    def items(self) -> Span[auto_readonly[int32]]:  # tpyc: warning(/non-polymorphic/)
        return self._extra


def read_base(b: readonly[Base]) -> None:
    s = b.items()  # tpyc: type(Span[readonly[int32]])
    print(s[int32(0)])


def read_child(c: readonly[Child]) -> None:
    s = c.items()  # tpyc: type(Span[readonly[int32]])
    print(s[int32(0)])
    print(s[int32(1)])


def main() -> None:
    b = Base()
    s = b.items()  # tpyc: type(Span[int32])
    print(s[int32(0)])
    read_base(b)

    c = Child()
    s2 = c.items()  # tpyc: type(Span[int32])
    print(s2[int32(0)])
    read_child(c)


main()
