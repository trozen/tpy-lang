# A borrowing getter off a temporary, bound past the statement, warns as its
# method twin does (BUGS.md#readonly-borrow-of-temporary-receiver); bind `h = mk()` first.
from tpy import Own, int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


G = [1, 2]


class Child:
    rec: Rec
    ys: list[int32]

    def __init__(self) -> None:
        self.rec = Rec(5)
        self.ys = [3]

    @property
    def data(self) -> list[int32]:
        return self.ys


class H:
    xs: list[int32]
    child: Child

    def __init__(self) -> None:
        self.xs = [1, 2]
        self.child = Child()

    @property
    def items(self) -> list[int32]:
        return self.xs

    @property
    def ch(self) -> Child:
        return self.child

    @property
    def mixed(self) -> list[int32]:
        if len(self.xs) > 1:
            return self.xs
        return G


def mk() -> Own[H]:
    return H()


def keep(r: Rec) -> Rec:
    return r


class Bag:
    def __add__(self, o: Rec) -> Rec:
        return o


def main() -> None:
    # local decl
    p = mk().items  # tpyc: warning(/Result borrows from temporary/)
    print("decl")
    # field read through the getter
    z = mk().ch.rec  # tpyc: warning(/Result borrows from temporary/)
    print("field_read")
    # an argument a bodied callee hands back
    r = keep(mk().ch.rec)  # tpyc: warning(/Result borrows from temporary/)
    print("kept_arg")
    # an operand a dunder hands back
    b = Bag()
    y = b + mk().ch.rec  # tpyc: warning(/Result borrows from temporary/)
    print("operand")
    # a getter lending its own field or a global
    q = mk().mixed  # tpyc: warning(/Result borrows from temporary/)
    print("mixed")
    # a getter chain: the inner getter lends the outer temporary's storage
    xs = mk().ch.data  # tpyc: warning(/Result borrows from temporary/)
    print("chain")
    hoisted()


def hoisted() -> None:
    try:
        # branch-first decl, predeclared above the `try`
        v = mk().items  # tpyc: warning(/Result borrows from temporary/)
    except ValueError:
        return
    print("hoisted")
