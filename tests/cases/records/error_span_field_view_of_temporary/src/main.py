# A Span field set from a slice of a fresh list keeps a view of a temporary
# that dies with the statement, so the method write rejects.
from tpy import Span, int32


def mk() -> list[int32]:
    return [1, 2, 3]


class Window:
    view: Span[int32]

    def __init__(self, xs: Span[int32]) -> None:
        self.view = xs

    def reset(self) -> None:
        # The slice's base is the fresh result of mk().
        self.view = mk()[0:2]  # tpyc: error(/Cannot bind Span\[int32\] field .view. to a temporary view source/)


def main() -> None:
    xs = [4, 5]
    w = Window(xs)
    w.reset()
    print(len(w.view))


main()
