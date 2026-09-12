# set |= requires identical element types -- subclass not accepted
# (mirrors error_set_update_subclass). Base defines __hash__/__eq__ so
# the set[Base] / set[Child] annotations pass the hashability gate and
# the intended subclass-narrowing check is reached.
from tpy import int32, uint64


class Base:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v

    def __hash__(self) -> uint64:
        return uint64(self.val)

    def __eq__(self, other: "Base") -> bool:
        return self.val == other.val


class Child(Base):
    extra: int32

    def __init__(self, v: int32, e: int32) -> None:
        super().__init__(v)
        self.extra = e


def main() -> None:
    src: set[Child] = set()
    dst: set[Base] = set()
    dst |= src  # tpyc: error(/Type mismatch.*expected Base, got Child/)


main()
