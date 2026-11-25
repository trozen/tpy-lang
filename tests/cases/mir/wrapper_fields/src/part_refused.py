# Wrapper fields and elements MIR keeps refusing, each with its reason. Binding a tuple field to a
# local (`p = h.t`) is a THIR reject (BUGS.md#tuple-field-local-bind-rejects), so no section holds one.
from tpy import StrView, int32, readonly


class Tok:
    s: StrView

    def __init__(self, s: StrView) -> None:  # tpyc: mir(covered)
        self.s = s


class Loaned:
    pair: tuple[Tok, int32]

    # a wrapper holding a loan: a loan stored at a member's depth has no stored-loan key (TODO.md
    # "one mechanism per value kind")
    def __init__(self, t: Tok) -> None:  # tpyc: mir(uncovered /record member holds a borrow/)
        self.pair = (t, 1)  # tpyc: warning(/copies Tok into field \(tuple element 0\)/)


class Nested:
    nest: tuple[int32, str] | None
    xs: list[str] | None

    # a wrapper or container inside a wrapper member
    def __init__(self) -> None:  # tpyc: mir(uncovered /unsupported record fields/)
        self.nest = None
        self.xs = None


# list and dict literals of tuples with an owned-leaf member: an element's temporary is a slot, and a
# slot's wrapper stores no owned-leaf member (the comprehension twins in part_elements are covered)
def literal() -> int32:  # tpyc: mir(uncovered /unsupported tuple scalar/)
    xs = [(1, "a" * 2), (2, "b" * 2)]
    return len(xs[1][1])


def dict_literal() -> int32:  # tpyc: mir(uncovered /unsupported tuple scalar/)
    d = {1: (1, "a" * 2)}
    return len(d[1][1])


# a tuple local with an owned-leaf member: THIR publishes no member layout
def local() -> int32:  # tpyc: mir(uncovered /missing or invalid layout fact/)
    t = (1, "a" * 3)
    return len(t[1])


# a loop variable bound to a tuple element
def loop_variable(xs: list[tuple[int32, str]]) -> int32:  # tpyc: mir(uncovered /wrapper loop variable needs local payload/)
    n = 0
    for t in xs:
        n += len(t[1])
    return n


# list-of-wrapper stub methods
def append(xs: list[tuple[int32, str]]) -> None:  # tpyc: mir(uncovered /stub parameter is not a readonly leaf/)
    xs.append((2, "b" * 2))


def pop(xs: list[tuple[int32, str]]) -> None:  # tpyc: mir(uncovered /unsupported expression type/)
    xs.pop()


def sort(xs: list[tuple[int32, int32]]) -> None:  # tpyc: mir(uncovered /stub protocol argument is not a builtin leaf/)
    xs.sort()


# a tuple key and a set of tuples
def tuple_key(d: dict[tuple[int32, int32], int32]) -> int32:  # tpyc: mir(uncovered /unsupported owned-leaf expression type/)
    return d[(1, 2)]


def set_member(xs: set[tuple[int32, int32]]) -> bool:  # tpyc: mir(uncovered /unsupported expression/)
    return (1, 2) in xs


# a loop variable bound to a tuple key
def tuple_keys(d: dict[tuple[int32, int32], int32]) -> int32:  # tpyc: mir(uncovered /wrapper loop variable needs local payload/)
    n = 0
    for k in d:
        n += 1
    return n


class RO:
    r: readonly[tuple[int32, int32]]

    # constructor: a readonly wrapper field is modeled, as a readonly record or container field is
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.r = (1, 2)


# a readonly wrapper field's member read
def ro_read(r: RO) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return r.r[0]


# an element record with a readonly field of any class is refused, a wrapper's included
def ro_elements(xs: list[RO]) -> None:  # tpyc: mir(uncovered /unsupported native container element/)
    xs.clear()


def main() -> None:
    print("refused literal", literal(), dict_literal(), local())
    xs = [(1, "a" * 2)]
    append(xs)
    pop(xs)
    print("refused loop", loop_variable(xs))
    ys = [(2, 1), (1, 2)]
    sort(ys)
    print("refused sort", ys[0][0])
    print("refused key", tuple_key({(1, 2): 3}), set_member({(1, 2)}))
    t = Tok("t")
    ld = Loaned(t)
    n = Nested()
    print("refused loaned", ld.pair[1], n.nest is None)
    rs = [RO()]
    print("refused ro_read", ro_read(rs[0]))
    ro_elements(rs)
    print("refused keys", tuple_keys({(1, 2): 3}), len(rs))
