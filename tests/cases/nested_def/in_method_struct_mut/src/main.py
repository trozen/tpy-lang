# A nested def STRUCTURALLY mutating self's container field (append):
# exercises the structural mutation replay; growth is visible on the
# caller's object.
from tpy import int32


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def fill(self, v: int32) -> None:
        def add() -> None:
            self.items.append(v)

        add()
        add()


def main() -> None:
    b = Bag()
    b.fill(9)
    print(b.items)


main()
