# Sibling module B: MIR verdict, callee summary and line-fact annotations.
from tpy import Own, StrView, int32

G = "global"


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


class Rec:
    # constructor: the str field is a copy of the view parameter
    def __init__(self, name: str) -> None:
        self.name = name  # tpyc: mir_copy(self.name)

    # method: a field write replaces the field's storage
    def rename(self, s: str) -> None:
        self.name = s  # tpyc: mir_write(self.name)


def total(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    n = 0
    for x in xs:
        n += x
    return n


def make(v: int32) -> Own[Box]:  # tpyc: mir(covered)
    return Box(v)


# a local view of a str parameter borrows it; the callee summary is known
def width(s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    v = s  # tpyc: mir_borrowed(v) mir_borrows(v, s)
    return len(v)


# a view rooted in a global: no summary can name its origin
def global_view() -> StrView:  # tpyc: mir_summary(opaque /origin outside the parameters/)
    return G


# a concatenation result is owned storage
def joined(a: str, b: str) -> int32:
    t = a + b  # tpyc: mir_owned(t)
    return len(t)


def main() -> None:
    print("part_b:", make(7).v, total([1, 2, 3]), width("abc"))
    r = Rec("old")
    r.rename("new")
    print("part_b rec:", r.name)
    print("part_b summary:", global_view(), joined("ab", "cd"))
