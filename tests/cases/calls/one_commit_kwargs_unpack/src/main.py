# A `**d` passed to a constructor's `**kwargs: Unpack[TD]` is an operand of its
# facts: inferred (`C(1, **d)`), explicit (`C[int32](1, **d)`) and plain (`D`).
from typing import TypedDict, Unpack
from tpy import int32


class Opt(TypedDict):
    n: int32


class C[T]:
    def __init__(self, v: T, **kw: Unpack[Opt]) -> None:
        kw["n"] += 1
        print("C kw:", kw["n"])


class D:
    def __init__(self, v: int32, **kw: Unpack[Opt]) -> None:
        kw["n"] += 10
        print("D kw:", kw["n"])


def inferred(d: Opt) -> None:
    # The constructor writes `kw`: `d` stays a mutable parameter (TPy passes
    # the dict itself, BUGS.md#dstar-kwargs-passes-the-dict).
    c = C(1, **d)


def explicit(d: Opt) -> None:
    # The explicit twin: the same parameter form.
    c = C[int32](1, **d)


def plain(d: Opt) -> None:
    # A plain constructor: the same parameter form.
    D(1, **d)


def main() -> None:
    inferred(Opt(n=1))
    explicit(Opt(n=5))
    plain(Opt(n=7))


main()
