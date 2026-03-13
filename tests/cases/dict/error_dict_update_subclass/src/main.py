# dict.update() requires identical value types -- subclass not accepted
# (dict values are stored by value; allowing Child where Base expected would slice elements)
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
    src: dict[str, Child] = {"a": Child(Int32(1), Int32(2))}
    dst: dict[str, Base] = {}
    dst.update(src)  # tpyc: error(/Type mismatch.*expected Base, got Child/)


main()
