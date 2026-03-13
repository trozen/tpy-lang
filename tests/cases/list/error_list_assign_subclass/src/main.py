# list literal assignment rejects subclass element type (covers PendingListType path)
# Storing Child in list[Base] would silently slice objects in C++
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
    items: list[Base] = [Child(Int32(1), Int32(2))]  # tpyc: error(/List literal element.*Child.*incompatible.*Base/)


main()
