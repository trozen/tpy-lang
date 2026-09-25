# A user @native value type declared borrowing_view=True is lifetime-checked
# like StrView: a cursor minted from a LOCAL buffer is rejected at the return.
from tpy import int32, ValueType
from tpy.extern import native


@native("::Cursor", borrowing_view=True)
class Cursor(ValueType):
    @native("get")
    def get(self) -> int32: ...


@native("::Buffer")
class Buffer:
    @native("cursor")
    def cursor(self) -> Cursor: ...


def cursor_of_local() -> Cursor:
    b = Buffer()
    return b.cursor()  # tpyc: error(/Cannot return Cursor referencing a local or temporary/)


def main() -> None:
    print(cursor_of_local().get())


main()
