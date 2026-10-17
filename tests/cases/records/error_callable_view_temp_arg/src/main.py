# A view bound from a `Callable` over a tuple temporary is a temporary view
# source, as its direct-call twin (BUGS.md#callable-view-tuple-temp-static-rejected).
from typing import Callable

from tpy import StrView, int32


def first_of(p: tuple[str, int32]) -> StrView:
    return p[0]


def callable_temp(f: Callable[[tuple[str, int32]], StrView], s: str) -> None:
    v: StrView = f((s, 1))  # tpyc: error(/Cannot bind StrView 'v' to a temporary view source/)
    print(v)


def main() -> None:
    callable_temp(first_of, "abc")


main()
