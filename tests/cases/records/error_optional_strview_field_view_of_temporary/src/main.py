# An Optional view field set from a slice of a fresh str keeps a view of a
# temporary that dies with the statement: the temporary-view rule rejects it.
from tpy import StrView


def mk() -> str:
    return "abcdef"


class Label:
    view: StrView | None

    def __init__(self) -> None:
        self.view = None

    def mark(self) -> None:
        # The slice's base is the fresh result of mk().
        self.view = mk()[1:3]  # tpyc: error(/Cannot bind StrView \| None field .view. to a temporary view source/)


def main() -> None:
    lb = Label()
    lb.mark()
    print(lb.view)


main()
