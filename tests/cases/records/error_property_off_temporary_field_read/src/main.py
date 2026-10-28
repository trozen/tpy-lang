# A field read through a getter off a temporary is refused (docs/PROPERTY_DESIGN.md
# sink table); legs below verified alone; global getter: BUGS.md#getter-global-off-temporary-rejects.
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Rec:
    x: int32
    sub: Box

    def __init__(self) -> None:
        self.x = 5
        self.sub = Box(5)


G: list[Box] = [Box(1)]


class Child:
    items: list[Box]

    def __init__(self) -> None:
        self.items = [Box(3)]

    @property
    def data(self) -> list[Box]:
        return self.items


class H:
    items: list[Box]
    child: Child
    r: Rec
    flag: bool

    def __init__(self) -> None:
        self.items = [Box(1), Box(2)]
        self.child = Child()
        self.r = Rec()
        self.flag = True

    @property
    def ch(self) -> Rec:
        return self.r

    @property
    def mixed(self) -> list[Box]:
        if self.flag:
            return self.items
        return G


def mk() -> Own[H]:
    return H()


def main() -> None:
    other = Rec()
    # The temporary's own record field; the temporary dies here.
    z = mk().ch.sub  # tpyc: warning(/borrows from temporary receiver/) error(/local_decl\.lends_from_temporary/)
    print(z.n)
    q = mk().mixed
    print(len(q))
    xs = mk().child.data
    print(len(xs))
    t = other if len(G) > 5 else mk().ch
    print(t.x)


main()
