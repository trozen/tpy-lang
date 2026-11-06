# A rebinding walrus at an owning sink copies where its `=` twin does, with
# the twin's warning; mutating the source afterwards shows the copy.
from tpy import Own, int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    a: A

    def __init__(self) -> None:
        self.a = A(1)


def size(x: list[int32]) -> int32:
    return len(x)


def take(x: Own[list[int32]]) -> Own[list[int32]]:
    x.append(9)
    return x


def borrowed_sink(h: H) -> None:
    x = A(0)
    l: list[A] = []
    # A borrowed source at an owning argument copies.
    l.append((x := h.a))  # tpyc: warning(/copies A into owned storage/)
    h.a.n = 50
    print("borrowed_sink", l[0].n, x.n)


def old_alias() -> None:
    x = A(0)
    y = x
    l: list[A] = []
    # An alias of the OLD object is live, so the walrus's object copies in at
    # `x`'s last use (its `=` twin moves: TODO.md, the local-binding item).
    l.append((x := A(7)))  # tpyc: warning(/copies A into owned storage/)
    y.n = 70
    print("old_alias", y.n, l[0].n)


def guard_alias(k: int32) -> None:
    x = A(1)
    y = x
    l: list[A] = []
    match k:
        case 1:
            # The arm's pass copies: the old object's alias is live.
            l.append(x)  # tpyc: warning(/copies A into owned storage/)
        case 2 if (x := A(7)).n > 0:
            print("guard_alias", len(l))
        case _:
            pass
    y.n += 5
    print("guard_alias", y.n, len(l), l[0].n)


def guard_list(k: int32) -> None:
    x = [1]
    keep = x
    match k:
        case 1 if size((x := [2])) > 0:
            pass
        case _:
            pass
    # The guard may not have run: `x` is still `keep`'s list, which copies.
    r = take(x)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("guard_list", len(r), len(keep))


def tuple_borrowed(q: A) -> tuple[Own[A], int32]:
    b = A(1)
    print("tuple_borrowed", b.n)
    # A borrowed tuple element copies, as the scalar does.
    return ((b := q), 1)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def tuple_finally_borrowed(q: A) -> tuple[Own[A], int32]:
    b = A(1)
    try:
        # A borrowed member under a mutating `finally` copies eagerly.
        return ((b := q), 7)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    finally:
        b.n = 99


def tuple_append_live() -> None:
    b = A(1)
    l: list[tuple[A, int32]] = []
    # `b` is read after: the element copies.
    l.append(((b := A(2)), 1))  # tpyc: warning(/copies A into owned storage/)
    b.n = 50
    print("tuple_append_live", l[0][0].n, b.n)


def main() -> None:
    borrowed_sink(H())
    old_alias()
    guard_alias(1)
    guard_list(0)
    q = A(3)
    t = tuple_borrowed(q)
    t[0].n = 31
    print("tuple_borrowed", q.n, t[0].n)
    q2 = A(3)
    u = tuple_finally_borrowed(q2)
    print("tuple_finally_borrowed", q2.n, u[0].n)
    tuple_append_live()


main()
