# Imported value globals read bare in every family: scalar, Final, a StrView
# global at an OWNED-str return, a container element, a shadowing local of the
# same name, and a read from a method body.
from tpy import int32
from helper import G, label, BIG, items


def read_g() -> int32:
    return G + 1


def read_final() -> int32:
    return BIG


def label_owned() -> str:
    return label  # a borrow-form view global copied into an owned str


def read_items() -> int32:
    return items[0]


def shadow() -> int32:
    G = 7  # a local of the same name -- the import is not seeded in this body
    return G


class C:
    x: int32

    def __init__(self) -> None:
        self.x = G

    def m(self) -> int32:
        return G * 2


def main() -> None:
    c = C()
    print(read_g(), read_final(), label_owned())
    print(read_items(), shadow(), c.m())


main()
