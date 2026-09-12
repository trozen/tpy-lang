# A **kwargs param has no default, so C++ requires every default before it to
# be dropped -- which means the call site must supply them.
from typing import TypedDict, Unpack

from tpy import int64


class Options(TypedDict):
    host: str


def scaled(a: int64, b: int64 = 5, **kwargs: Unpack[Options]) -> int64:
    return a * 10 + b


def spanned(a: int64, b: int64 = 2, c: int64 = 3, **kwargs: Unpack[Options]) -> int64:
    return a * 100 + b * 10 + c


def main() -> None:
    print(scaled(1, host="x"))
    print(scaled(1, 7, host="x"))
    print(spanned(1, host="y"))
    print(spanned(1, 8, host="y"))
    print(spanned(1, 8, 9, host="y"))


main()
