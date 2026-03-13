# set |= requires identical element types -- subclass not accepted (mirrors error_set_update_subclass)
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
    dst |= src  # tpyc: error(/Type mismatch.*expected Base, got Child/)


main()
