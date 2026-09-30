# A StrView field set from a slice of a fresh str keeps a view of a temporary
# that dies with the statement, so the method write rejects.
from tpy import StrView


def mk(n: int) -> str:
    return "abcdef"[:n]


class Label:
    view: StrView

    def __init__(self) -> None:
        self.view = "d"

    def mark(self, n: int) -> None:
        # The slice's base is the fresh result of mk(n).
        self.view = mk(n)[1:4]  # tpyc: error(/Cannot bind StrView field .view. to a temporary view source/)


def main() -> None:
    lb = Label()
    lb.mark(5)
    print(lb.view)


main()
