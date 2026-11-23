# A tuple member holding a view: its loan is keyed under the member, so a
# rebind through the borrow is seen through the tuple.
from tpy import StrView, int32


class T:
    s: StrView

    def __init__(self, s: StrView) -> None:
        self.s = s


def view_alias(a: str, b: str) -> int32:  # tpyc: mir(certified)
    t = (T(a), 1)
    r = t[0]  # tpyc: mir_borrows(r, t[0])
    r.s = b
    return len(t[0].s)


def main() -> None:
    print("view", view_alias("ab", "cde"))
