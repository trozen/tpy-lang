# Returning the result of a method that returns a borrowing view from
# self.field must be safe -- the caller's reference traces through the
# auto-inferred borrow contract back to the param.

from tpy import StrView


class Wrapper:
    def __init__(self, s: str) -> None:
        self.s = s

    def get_view(self) -> StrView:
        return self.s


def borrow_through_method(w: Wrapper) -> StrView:
    return w.get_view()  # tpyc: ok


def main() -> None:
    w = Wrapper("hello")
    print(borrow_through_method(w))


main()
