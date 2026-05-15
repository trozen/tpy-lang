# set |= requires identical element types -- subclass not accepted
# (mirrors error_set_update_subclass). Base defines __hash__/__eq__ so
# the set[Base] / set[Child] annotations pass the hashability gate and
# the intended subclass-narrowing check is reached.
from tpy import Int32, UInt64


class Base:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def __hash__(self) -> UInt64:
        return UInt64(self.val)

    def __eq__(self, other: "Base") -> bool:
        return self.val == other.val


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
