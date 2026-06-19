# A ctor field initializer reading a narrowed optional-ptr param via member
# access lowers `->` in the member-init list; a None-check field stays `!= nullptr`.
class Node:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Holder:
    found: int
    present: bool

    def __init__(self, n: Node | None) -> None:
        self.found = n.v if n is not None else -1
        self.present = n is not None


def main() -> None:
    h = Holder(Node(5))
    print(h.found, h.present)
    e = Holder(None)
    print(e.found, e.present)


main()
