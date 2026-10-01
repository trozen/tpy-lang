# A read-only owned-element tuple param warns "never consumed" (like a scalar
# Own[T]); the escape hatch is the borrow form tuple[A, A], which does not
# warn (read_borrow). The warning is suppressed for @nocopy elements
# (consume-by-drop is legitimate), so read_nocopy does not warn either.
# Once an unpack at the last use moves the elements into locals, each element
# is a scalar Own of its own: a dropped one warns, naming its local. A local
# that took an Own (moved in, or unpacked) consumes it when that local is
# stored in a field, forwarded or returned.
from tpy import Own, nocopy, int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class B:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


def keep(x: Own[A], into: list[A]) -> None:
    into.append(x)


def keep_both(p: tuple[Own[A], Own[A]], into: list[A]) -> None:
    a, b = p
    into.append(a)
    into.append(b)


def read_owned(p: tuple[Own[A], Own[A]]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return p[0].n + p[1].n


def read_borrow(p: tuple[A, A]) -> int32:  # tpyc: ok
    return p[0].n + p[1].n


def read_nocopy(p: tuple[Own[B], Own[B]]) -> int32:  # tpyc: ok
    return p[0].m + p[1].m


# free function: element 1 is unpacked into `b` and dropped.
def drop_one(p: tuple[Own[A], Own[A]], into: list[A]) -> int32:  # tpyc: warning(/Own\[A\] element 1 of tuple param 'p' is never consumed \(unpacked into 'b'/)
    a, b = p
    keep(a, into)
    return b.n


class H:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    # method: the same per-element verdict as the free function.
    def drop_first(self, p: tuple[Own[A], Own[A]], into: list[A]) -> int32:  # tpyc: warning(/Own\[A\] element 0 of tuple param 'p' is never consumed \(unpacked into 'a'/)
        a, b = p
        keep(b, into)
        self.k += 1
        return a.n + self.k


# branch: element 1 is consumed on one path only, as the scalar would warn.
def drop_on_branch(p: tuple[Own[A], Own[A]], c: bool, into: list[A]) -> None:  # tpyc: warning(/element 1 of tuple param 'p' is never consumed/)
    a, b = p
    keep(a, into)
    if c:
        keep(b, into)


# mixed owned+borrow: an ownership transfer like the owned twin, so a
# param nothing consumes warns the same way.
def mixed(p: tuple[Own[A], A]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return p[0].n + p[1].n


# scalar: `y` took what `x` moved in, so consuming `y` consumes `x`.
def through_local(x: Own[A], into: list[A]) -> None:  # tpyc: ok
    y = x
    keep(y, into)


# every element consumed through its local.
def consume_all(p: tuple[Own[A], Own[A]], into: list[A]) -> None:  # tpyc: ok
    a, b = p
    keep(a, into)
    keep(b, into)


# a dropped @nocopy element is consume-by-drop, as for the scalar.
def drop_nocopy(p: tuple[Own[A], Own[B]], into: list[A]) -> int32:  # tpyc: ok
    a, b = p
    keep(a, into)
    return b.m


# a value element carries no ownership to drop.
def drop_value(p: tuple[Own[A], int32], into: list[A]) -> int32:  # tpyc: ok
    a, k = p
    keep(a, into)
    return k


# the whole tuple forwarded to another owned tuple param is consumed.
def forward(p: tuple[Own[A], Own[A]], into: list[A]) -> None:  # tpyc: ok
    keep_both(p, into)


class Holder:
    slot: A
    other: A

    def __init__(self) -> None:
        self.slot = A(0)
        self.other = A(0)

    # field store: storing the local `y` consumes the `x` it took.
    def store_through_local(self, x: Own[A]) -> None:  # tpyc: ok
        y = x
        self.slot = y

    # field store: each stored unpack target consumes its element.
    def store_unpacked(self, p: tuple[Own[A], Own[A]]) -> None:  # tpyc: ok
        a, b = p
        self.slot = a
        self.other = b


# return: returning the local `y` consumes the `x` it took.
def return_through_local(x: Own[A]) -> Own[A]:  # tpyc: ok
    y = x
    return y


# return: the returned unpack target consumes its element.
def return_unpacked(p: tuple[Own[A], Own[A]], into: list[A]) -> Own[A]:  # tpyc: ok
    a, b = p
    keep(a, into)
    return b


def main() -> None:
    print(read_owned((A(1), A(2))))
    a = A(3)
    b = A(4)
    print(read_borrow((a, b)))
    print(read_nocopy((B(5), B(6))))
    kept: list[A] = []
    print("drop_one", drop_one((A(1), A(2)), kept))
    print("drop_first", H().drop_first((A(3), A(4)), kept))
    drop_on_branch((A(5), A(6)), False, kept)
    print("mixed", mixed((A(7), a)))
    through_local(A(8), kept)
    consume_all((A(9), A(10)), kept)
    print("drop_nocopy", drop_nocopy((A(11), B(12)), kept))
    print("drop_value", drop_value((A(13), 14), kept))
    forward((A(15), A(16)), kept)
    print("kept", [x.n for x in kept])
    h = Holder()
    h.store_through_local(A(17))
    h.store_unpacked((A(18), A(19)))
    print("store", h.slot.n, h.other.n)
    kept2: list[A] = []
    print("return", return_through_local(A(20)).n,
          return_unpacked((A(21), A(22)), kept2).n, [x.n for x in kept2])


main()
