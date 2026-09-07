# `print(self)` inside a method: the receiver is a value position, so it is
# dereferenced and streamed raw through the record's own printer.
from tpy import Int32


class W:
    n: Int32

    def __init__(self) -> None:
        self.n = 1

    def __str__(self) -> str:
        return "W(" + str(self.n) + ")"

    def show(self) -> None:
        # The whole receiver is the print argument.
        print(self)


def main() -> None:
    w = W()
    w.show()
    w.n = 4
    w.show()


main()
