# list literal assignment rejects subclass element type (covers PendingListType path)
# Storing Child in list[Base] would silently slice objects in C++
from tpy import int32


class Base:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Child(Base):
    extra: int32

    def __init__(self, v: int32, e: int32) -> None:
        super().__init__(v)
        self.extra = e


def main() -> None:
    items: list[Base] = [Child(int32(1), int32(2))]  # tpyc: error(/List literal element.*Child.*incompatible.*Base/)


main()
