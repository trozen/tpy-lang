# Keyword captures of value-repr Optional fields on the union tier: the scalar
# capture's narrowed print and the view capture's narrowed concat.
from typing import Optional
from tpy import Int32


class WithScalar:
    n: Optional[Int32]

    def __init__(self, n: Optional[Int32]) -> None:
        self.n = n


class WithStr:
    s: Optional[str]

    def __init__(self, s: Optional[str]) -> None:
        self.s = s


class Other:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def show(u: WithScalar | WithStr | Other) -> None:
    match u:
        case WithScalar(n=x):
            if x is not None:
                print(x)
        case WithStr(s=t):
            if t is not None:
                print("s=" + t)
        case Other(v=v):
            print(v)


def main() -> None:
    show(WithScalar(4))
    show(WithStr("hi"))
    show(Other(9))


main()
