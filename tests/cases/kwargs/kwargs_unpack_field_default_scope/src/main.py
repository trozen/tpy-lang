# A TypedDict field default naming an enum member is the member it names
# where the TypedDict is written, also at a call whose scope binds the
# enum's name to something else.
import enum
from typing import TypedDict, Unpack
from tpy import int32


class E(enum.Enum):
    A = 1
    B = 2


class Opts(TypedDict):
    n: int32
    level: E = E.B  # tpyc: warning(/ignored by CPython/)


def opts(**kw: Unpack[Opts]) -> int32:
    return kw["level"].value * 10 + kw["n"]


def main(E: int32) -> None:
    print(opts(n=E))  # the omitted field default fills here


main(9)
