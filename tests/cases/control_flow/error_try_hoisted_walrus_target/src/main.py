# A walrus target hoisted out of a `try` keeps the forward-declared hoist model,
# which the walrus lowering does not mirror.
from tpy import Int32


class Holder:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def view(self) -> list[Int32]:
        return self.items


def use(h: Holder) -> Int32:
    try:
        if len(v := h.view()) > 0:  # tpyc: error(/expr.walrus/)
            v.append(9)
    except ValueError:
        return -1
    return len(h.items)


def main() -> None:
    print(use(Holder()))


main()
