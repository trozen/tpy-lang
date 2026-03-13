# set.update() requires identical element types -- subclass not accepted
# (set elements stored by value; Child where Base expected would slice elements)
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
    src: set[Child] = set()
    dst: set[Base] = set()
    dst.update(src)  # tpyc: error(/Type mismatch.*expected Base, got Child/)


main()
