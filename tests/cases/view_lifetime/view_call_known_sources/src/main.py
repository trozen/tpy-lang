# A view bound from a view-returning call over a temporary argument when
# the callee names no operand its view reads: the view is the callee's.
from typing import Callable

from tpy import StrView


def above(s: str) -> StrView:
    return "above"


def make() -> Callable[[str], StrView]:
    return above


class Label:
    def text(self, s: str) -> StrView:
        return "method"


def main() -> None:
    n = 1
    # free function defined above: its recorded sources are known empty
    v: StrView = above(f"a {n}")  # tpyc: ok
    print("free_above:", v)
    # free function defined below: its body is not analyzed yet
    u: StrView = below(f"b {n}")  # tpyc: ok
    print("free_below:", u)
    # method with known-empty sources: the view is its receiver's at most
    lab = Label()
    w: StrView = lab.text(f"m {n}")  # tpyc: ok
    print("method:", w)
    # call through a callee expression: no name to read a fact off
    x: StrView = make()(f"e {n}")  # tpyc: ok
    print("expr_callee:", x)


def below(s: str) -> StrView:
    return "below"


main()
