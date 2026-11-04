# Error: a step returned through a lending call dangles past its step
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ident(p: P) -> P:
    return p


def src(ps: list[P]) -> Iterator[P]:
    for p in ps:
        yield p


def first(ps: list[P]) -> P:
    for x in src(ps):
        return ident(x)  # tpyc: error(/Cannot return local or temporary as reference/)
    return ps[0]


def main() -> None:
    print(first([P(1)]).n)


main()
