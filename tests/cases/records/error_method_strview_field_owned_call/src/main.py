# A StrView field set from a method returning an owned str keeps a view of
# the temporary result: the temporary-view rule rejects it.
from tpy import StrView


class Label:
    view: StrView

    def __init__(self) -> None:
        self.view = "d"

    def mark(self, a: str) -> None:
        # `upper()` returns a fresh str.
        self.view = a.upper()  # tpyc: error(/Cannot bind StrView field .view. to a temporary view source/)


def main() -> None:
    lb = Label()
    lb.mark("q")
    print(lb.view)


main()
