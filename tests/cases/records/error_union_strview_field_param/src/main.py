# A view param stored into the view member of a union field by a method:
# the body write rejects (BUGS.md#record-view-field-escapes-local-buffer).
from tpy import StrView, int32


class Label:
    view: StrView | int32

    def __init__(self) -> None:
        self.view = 0

    def mark(self, s: StrView) -> None:
        # The view member would keep pointing into the caller's buffer.
        self.view = s  # tpyc: error(/field_write\.lift\.borrow/)


def main() -> None:
    lb = Label()
    lb.mark("abc")
    print("done")


main()
