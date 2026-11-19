# A loop or a stored loan holding the caller's storage across a yield: the body is
# analyzed, but MIR proves nothing between two steps until the caller's half lands.
from typing import Iterator

from tpy import StrView, int32


# param_list: the cursor over the caller's list is open while the frame is suspended
def doubled(items: list[int32]) -> Iterator[int32]:  # tpyc: mir(incomplete /suspension: loan of caller storage held across a suspension/)
    for x in items:
        yield x * 2


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [4, 5]

    # receiver_field: the cursor walks the receiver's field
    def __iter__(self) -> Iterator[int32]:  # tpyc: mir(incomplete /suspension: loan of caller storage held across a suspension/)
        for x in self.xs:
            yield x


class Tok:
    s: StrView

    def __init__(self, s: str) -> None:
        self.s = s

    def reset(self, s: str) -> None:
        self.s = s


class Buf:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text


# stored_loan: a loan the body stores into a parameter's object, held across a yield
def retoken(t: Tok, b: Buf) -> Iterator[int32]:  # tpyc: mir(incomplete /suspension: loan of caller storage held across a suspension/)
    t.reset(b.text)
    yield 1
    yield len(t.s)


# stored_literal: a literal's static storage stored into a parameter's object is no caller storage
def retoken_literal(t: Tok) -> Iterator[int32]:  # tpyc: mir(covered)
    t.reset("literal")
    yield 1
    yield len(t.s)


def main() -> None:
    print("param_list:", list(doubled([1, 2, 3])))
    print("receiver_field:", list(Bag()))
    t = Tok("initial")
    b = Buf("hello")
    print("stored_loan:", list(retoken(t, b)))
    print("stored_literal:", list(retoken_literal(Tok("initial"))))
