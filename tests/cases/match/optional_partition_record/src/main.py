# Optional-partition match over pointer-repr Optional[record] subjects: a
# None prefix arm + one narrowed arm off the __match_inner_N deref alias
# (bare class / capture / as / no-None-arm shapes, param and local subjects).
# Captures alias the subject's target: the write through the subject is
# observed through the capture and by the caller (reference semantics).
from tpy import Int32


class Leaf:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n


class Holder:
    opt: Leaf | None

    def __init__(self):
        self.opt = None


def show(x: Leaf | None) -> None:
    match x:
        case None:
            print("none")
        case Leaf():
            print(x.n)


def bump_and_peek(x: Leaf | None) -> None:
    match x:
        case None:
            print("none")
        case v:
            x.n += 10
            print(v.n)


def label(x: Leaf | None) -> Int32:
    match x:
        case None:
            return -1
        case Leaf() as v:
            return v.n


def peek(x: Leaf | None) -> None:
    match x:  # tpyc: warning(/non-exhaustive/)
        case Leaf():
            print(x.n)


def from_field(h: Holder) -> None:
    q = h.opt
    match q:
        case None:
            print("empty")
        case Leaf():
            print(q.n)


def main() -> None:
    leaf = Leaf(3)
    show(leaf)
    show(None)
    bump_and_peek(leaf)
    print(leaf.n)
    print(label(leaf))
    print(label(None))
    peek(leaf)
    h = Holder()
    from_field(h)
    h.opt = Leaf(7)
    from_field(h)


main()
