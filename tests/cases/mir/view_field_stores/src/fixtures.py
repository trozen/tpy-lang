# Shared records and helpers of the view_field_stores sections.
from tpy import StrView, copy, int32


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: str, n: int32) -> None:
        self.s = s
        self.n = n

    # method: stores its argument's loan in the receiver (a published transfer), replacing the old one
    def reset(self, s: str) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.s = s  # tpyc: mir_borrows(self.s, s)

    # method: a conditional store is a may-store
    def maybe(self, s: str, f: bool) -> None:  # tpyc: mir(covered) mir_summary(known)
        if f:
            self.s = s


class Outer:
    inner: Tok
    tag: int32

    # constructor: a member record holding a view, copied from the lent argument
    def __init__(self, inner: Tok, tag: int32) -> None:  # tpyc: mir(covered)
        self.inner = copy(inner)
        self.tag = tag


class Stamp:
    s: StrView
    n: int32

    # constructor: a view member written in the body, after member initialization
    def __init__(self, s: str, n: int32) -> None:  # tpyc: mir(covered)
        self.s = "lit"
        self.n = n
        if n > 0:
            self.s = s  # tpyc: mir_borrows(self.s, s)


class R:
    buf: str
    s: StrView

    def __init__(self, buf: str, s: StrView) -> None:
        self.buf = buf
        self.s = s

    # method: an owned-field setter on a record holding a view stores no loan; the caller's
    # own stored loan is the caller's to check (through the published write)
    def set_buf(self, k: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.buf = mk(k)


class Buf:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text


def mk(k: int32) -> str:
    return "x" * (40 + k)


class Kid(Tok):
    pass
