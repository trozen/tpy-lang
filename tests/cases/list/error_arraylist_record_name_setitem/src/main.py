# A copy-shaped record NAME at an ArrayList element slot: the `Own[T]` slot binds
# an rvalue or a move source, never a name that would have to copy.
from tpy import Int32
from tplib import ArrayList


class Row:
    x: Int32

    def __init__(self) -> None:
        self.x = 1


def fill() -> None:
    a = ArrayList[Row, 4]()
    a.append(Row())
    z = Row()
    a[0] = z  # tpyc: error(/setitem.record_own_copy/)
    print(z.x)


def main() -> None:
    fill()


main()
