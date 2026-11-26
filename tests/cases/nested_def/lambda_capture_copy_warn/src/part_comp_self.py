# comp_self: a comprehension target spelled `self` in a free function is an
# ordinary binding: the lambda copies the element it names, so a later
# mutation of that element is invisible to it.
from typing import Callable
from tpy import int32


class Q:
    def __init__(self, n: int32) -> None:
        self.n = n


def comp_self(qs: list[Q]) -> None:
    # The subject: each closure holds its own copy of `self`'s element.
    fs: list[Callable[[], int32]] = [lambda: self.n for self in qs]  # tpyc: warning(/copies the object 'self'/) warning(/'self' is bound again/)
    qs[-1].n = 50
    print("comp_self:", fs[-1](), qs[-1].n)


def main() -> None:
    comp_self([Q(1), Q(2)])
