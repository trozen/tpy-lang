# The fresh objects an operand reads through call arguments: a lent
# argument of a subclass, conversions a `str` argument renders bare, two
# lent arguments, a receiver and an argument, an owner with a view field,
# a callee writing the root it lends.
from tpy import int32, Own, StrView
from fixtures import P, show


class S(P):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class H:
    s: str

    def __init__(self) -> None:
        self.s = "h" * 40

    def pick(self, t: str) -> StrView:
        if len(t) < 3:
            return self.s
        return t


class Owner:
    name: str
    view: StrView

    def __init__(self, s: str) -> None:
        self.name = s + "-owned-long-heap-string-0123456789"
        self.view = "static"

    def get_name(self) -> StrView:
        return self.name


def mks(n: int32) -> Own[S]:
    return S(n)


def mkh() -> Own[H]:
    return H()


def make(s: str) -> Own[Owner]:
    return Owner(s)


def pick(p: P) -> P:
    return p


def take_owner(o: Owner) -> StrView:
    return o.name


def mkp(n: int32) -> Own[P]:
    return P(n)


def bump2(a: P, b: P) -> P:
    a.n += 1
    b.n += 1
    return a if a.n > b.n else b


def head(s: str) -> StrView:
    return s[0:30]


def pick2(a: str, b: str) -> StrView:
    return a if len(a) > len(b) else b


def choose(flag: bool, a: str, b: str) -> StrView:
    if flag:
        return a
    return b


def subclass(name: str) -> None:
    # A lent argument converted to its parameter's base class.
    sub_r = pick(mks(0)).n or 7  # tpyc: ok
    print("roots-sub", sub_r)
    sub_in_r = pick(mks(2)).n in (1, 2)  # tpyc: ok
    print("roots-sub-in", sub_in_r)


def conversions(name: str) -> None:
    fb: StrView = name
    # A `str` argument's view of a string, nested twice.
    show("roots-head2", head(head(name + "-twice-long-heap-string-0123456789")))  # tpyc: ok
    v = head(head(name + "-sel-long-heap-string-0123456789")) or fb  # tpyc: ok
    print("roots-head2-sel", v)
    r = head(head(name + "-in-long-heap-string-0123456789")) in ("q", "r")  # tpyc: ok
    print("roots-head2-in", r)


def operands(name: str, flag: bool) -> None:
    fb: StrView = name
    # Two lent arguments, both seated in argument order.
    show("roots-two", choose(flag, name + "-a-long-heap-string-0123456789",  # tpyc: ok
                             name + "-b-long-heap-string-0123456789"))
    w = pick2(name + "-c-long-heap-string-0123456789", name) or fb  # tpyc: ok
    print("roots-two-sel", w)
    # A receiver and an argument, both seated.
    show("roots-recv", mkh().pick(name + "-d-long-heap-string-0123456789"))  # tpyc: ok
    x = mkh().pick(name + "-e-long-heap-string-0123456789") or fb  # tpyc: ok
    print("roots-recv-sel", x)
    # A short argument: the receiver's own storage is the value.
    y = mkh().pick(name) or fb  # tpyc: ok
    print("roots-recv-own", y)
    # An owner with a view field: the operand reads its owned field.
    show("roots-owner", make(name).name.strip())  # tpyc: ok
    print("roots-owner-in", make(name).name.strip() in (  # tpyc: ok
        name + "-owned-long-heap-string-0123456789", "q"))
    # The owner's storage lent by a method and by a free function.
    show("roots-owner-m", make(name).get_name())  # tpyc: ok
    m = make(name).get_name() or fb  # tpyc: ok
    print("roots-owner-m-sel", m)
    print("roots-owner-m-in", make(name).get_name() in (  # tpyc: ok
        name + "-owned-long-heap-string-0123456789", "q"))
    show("roots-owner-f", take_owner(make(name)))  # tpyc: ok
    f = take_owner(make(name)) or fb  # tpyc: ok
    print("roots-owner-f-sel", f)


def writes() -> None:
    p = P(5)
    # The callee writes the seated root before the operand reads it.
    r = bump2(p, mkp(1)).n or 7  # tpyc: ok
    print("roots-bump", r, p.n)
    q = P(1)
    # The operand reads the root itself, written by the callee.
    s = bump2(mkp(9), q).n or 7  # tpyc: ok
    print("roots-bump-q", s, q.n)


def main() -> None:
    subclass("ab")
    conversions("ab")
    operands("ab", True)
    operands("cd", False)
    writes()
