# A StrView field cannot keep a freshly built str written by a method: the view
# would outlive the concatenation it points into, so the write rejects.
from tpy import StrView


class Label:
    view: StrView

    def __init__(self) -> None:
        self.view = "d"

    def mark(self, a: str) -> None:
        self.view = a + "!"  # tpyc: error(/assign.field_write_shape/)


def main() -> None:
    lb = Label()
    lb.mark("q")
    print(lb.view)


main()
