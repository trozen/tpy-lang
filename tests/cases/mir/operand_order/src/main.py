# MIR's operand-order checks: each refuses a writing operand whose C++ order against the
# other operands is unspecified, and keeps a neighbour writing nothing.
from tpy import int32


class Rec:
    s: str
    n: int32

    def __init__(self) -> None:
        self.s = "ab"
        self.n = 0

    # field str-append: the appended call writes self while the field is read
    def append_writing(self) -> None:  # tpyc: mir(uncovered /order-sensitive eager operands/)
        self.s += word(self)

    def append_pure(self) -> None:  # tpyc: mir(covered)
        self.s += tag(self)


def word(h: Rec) -> str:
    h.n += 1
    return "x"


def tag(h: Rec) -> str:
    return "y" * h.n


def bump(h: Rec) -> int32:
    h.n += 1
    return h.n


def peek(h: Rec) -> int32:
    return h.n


# str-slice bound: the bound's call writes the record whose field is sliced
def slice_writing(h: Rec) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    return len(h.s[bump(h):])


def slice_pure(h: Rec) -> int32:  # tpyc: mir(covered)
    return len(h.s[peek(h):])


def lst(h: Rec, xs: list[int32]) -> list[int32]:
    h.n += 1
    return xs


def view(h: Rec, xs: list[int32]) -> list[int32]:
    return xs


# a borrowed container a call returns, indexed: the call writes what the index reads
def call_place_writing(h: Rec, xs: list[int32]) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    return lst(h, xs)[h.n]


def call_place_pure(h: Rec, xs: list[int32]) -> int32:  # tpyc: mir(covered)
    return view(h, xs)[h.n]


class PW:
    x: int32

    def __init__(self, h: Rec) -> None:
        self.x = h.n
        h.n += 1


class PP:
    x: int32

    def __init__(self, h: Rec) -> None:
        self.x = h.n


# a conditionally built record's named temporary: its constructor writes the record it is
# given, while the same shape with a reading constructor stays covered
def select_writing(h: Rec, q: PW, flag: bool) -> int32:  # tpyc: mir(uncovered /named temporary needs stable scalar operands/)
    r = PW(h) if flag else q
    return r.x


def select_pure(h: Rec, q: PP, flag: bool) -> int32:  # tpyc: mir(covered)
    r = PP(h) if flag else q
    return r.x


def main() -> None:
    h = Rec()
    h.append_writing()
    h.append_pure()
    print("append:", h.s)
    print("slice:", slice_writing(h), slice_pure(h))
    # equal elements: the printed value does not depend on which operand C++ evaluates first
    xs = [5, 5, 5, 5, 5, 5]
    print("call_place:", call_place_writing(h, xs), call_place_pure(h, xs))
    print("select:", select_writing(h, PW(h), True), select_pure(h, PP(h), True))


main()
