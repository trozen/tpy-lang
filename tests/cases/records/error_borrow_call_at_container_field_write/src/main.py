# A BORROW-returning free call at a container FIELD write: the owned-prvalue row
# is scoped to owning returns, so the borrow source stays out. Concretely,
# `self.data = first_of(h)` writes a borrowed list into a container field;
# TPy rejects that assignment today.
from tpy import Int32


class Holder:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = []

    def peek(self) -> list[Int32]:
        return self.items


def first_of(h: Holder) -> list[Int32]:
    return h.items


class Sink:
    data: list[Int32]

    def __init__(self) -> None:
        self.data = []

    def fill(self, h: Holder) -> None:
        self.data = first_of(h)  # tpyc: error(/call.ret_type.container/)


def main() -> None:
    s = Sink()
    h = Holder()
    h.items.append(42)
    s.fill(h)
    print(len(s.data))


main()
