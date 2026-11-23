from tpy import int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class C[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []


type Tree = int32 | A | list[Tree]
type GTree[T] = C[T] | list[GTree[T]]


def bump(t: Tree) -> int32:
    match t:
        case A() as a:
            a.n += 100
            return a.n
        case list() as xs:
            xs.append(0)
            return len(xs)
        case _:
            return -1


def peek(t: Tree) -> int32:
    if isinstance(t, A):
        return t.n
    return 0


def gbump(t: GTree[A]) -> int32:
    match t:
        case C() as c:
            c.items[0].n += 100
            return c.items[0].n
        case _:
            return -1


class M:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    def bump(self, t: Tree) -> int32:
        return bump(t)
