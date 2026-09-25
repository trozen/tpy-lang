# borrowing_view is a fact about a type, so a function-level @native rejects it.
from tpy import int32
from tpy.extern import native


@native("::cursor_pos", borrowing_view=True)  # tpyc: error(/borrowing_view=...\) is only valid on a class/)
def cursor_pos() -> int32: ...


def main() -> None:
    print(cursor_pos())


main()
