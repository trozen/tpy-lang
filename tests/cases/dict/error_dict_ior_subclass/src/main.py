# dict |= requires identical value types -- subclass not accepted (mirrors error_dict_update_subclass)
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
    src: dict[str, Child] = {"a": Child(int32(1), int32(2))}
    dst: dict[str, Base] = {}
    dst |= src  # tpyc: error(/Type mismatch.*expected Base, got Child/)


main()
