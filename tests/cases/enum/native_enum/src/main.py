# @native binding to an existing C++ enum class; tests print, match, .name, .value.
# Uses auto() so the C++ side is the source of truth for member values
# (declared C++-side as A=10, B=20 to prove TPy doesn't depend on a value mirror).
# tpy: include("native_types.hpp")
from enum import Enum, auto
from tpy.extern import native


@native("ns::E")
class E(Enum):
    A = auto()
    B = auto()


def label(e: E) -> str:
    match e:
        case E.A:
            return "first"
        case E.B:
            return "second"


def main() -> None:
    e = E.A
    print(e)
    print(E.B)
    print(label(e))
    print(label(E.B))
    print(e.name)
    print(e.value)


main()
