# A `readonly[T]` return hands the caller a CONST borrow, so the source it
# borrows from keeps no write grant: the param stays `const T&`, the enclosing
# method keeps its auto-const, and a caller that only reads the borrow stays
# const too. The constness is pinned by the committed .hpp; the sections here
# pin the aliasing half -- each borrow is BOUND before the owner is mutated and
# read again after, so a silent copy would print the stale value.
from tpy import int32, readonly


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Reg:
    p: Point

    def __init__(self, x: int32) -> None:
        self.p = Point(x)

    # method position: the returned borrow is const, so `self` is not mutated
    @readonly
    def view(self) -> readonly[Point]:  # tpyc: ok
        return self.p

    def bump(self) -> None:
        self.p.x += 10


class Reader:
    reg: Reg

    def __init__(self, x: int32) -> None:
        self.reg = Reg(x)

    # caller position: reading a readonly borrow through a field leaves the
    # caller's own receiver unmutated, so `read` is still inferred const
    def read(self) -> int32:
        v = self.reg.view()  # tpyc: ok
        return v.x


# free function: the param is returned as a const borrow
def view_items(items: list[int32]) -> readonly[list[int32]]:  # tpyc: ok
    return items


# transitive: the call edge carries the same verdict to this param
def sum_items(items: list[int32]) -> int32:
    total = int32(0)
    for y in view_items(items):
        total += y
    return total


# tuple return: the readonly leg that already granted no write, kept as sibling
def first_two(items: list[Point]) -> readonly[tuple[Point, Point]]:  # tpyc: ok
    return (items[0], items[1])


def main() -> None:
    r = Reg(1)
    # the binding is kept LIVE across the mutation: re-invoking the accessor
    # would read the field afresh and could not tell a borrow from a copy
    v = r.view()
    print("method:", v.x)
    r.bump()
    print("method after bump:", v.x)

    rd = Reader(5)
    print("caller:", rd.read())
    rd.reg.bump()
    print("caller after bump:", rd.read())

    xs = [1, 2, 3]
    # the container borrow binds a const alias (`const std::vector<T>&`),
    # kept live across the write like the record's
    vs = view_items(xs)  # tpyc: ok
    print("free:", vs[0], sum_items(xs))
    xs[0] = 40
    print("free after write:", vs[0], sum_items(xs))

    ps = [Point(7), Point(8)]
    pair = first_two(ps)
    print("tuple:", pair[0].x, pair[1].x)
    ps[0].x = 70
    print("tuple after write:", pair[0].x)


main()
