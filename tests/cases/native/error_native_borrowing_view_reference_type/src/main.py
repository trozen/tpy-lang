# borrowing_view=True declares that the values of a VALUE type are borrow
# handles; on a reference type (no ValueType marker) it is rejected.
from tpy import int32
from tpy.extern import native


@native("::Cursor", borrowing_view=True)
class Cursor:  # tpyc: error(/borrowing_view=True\) requires a value type/)
    @native("get")
    def get(self) -> int32: ...


def main() -> None:
    pass


main()
