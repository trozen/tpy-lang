# list.append / insert / __setitem__ reject subclass element type (covers OwnType path)
# Storing Child in list[Base] via mutation would silently slice objects in C++
from tpy import Int32


class Base:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


class Child(Base):
    extra: Int32

    def __init__(self, v: Int32, e: Int32) -> None:
        super().__init__(v)
        self.extra = e


def main() -> None:
    items: list[Base] = []
    items.append(Child(Int32(1), Int32(2)))  # tpyc: error(/Type mismatch.*expected Base, got Child/)


main()
