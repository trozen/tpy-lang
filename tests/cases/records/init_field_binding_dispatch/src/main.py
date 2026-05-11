# Field-init RHS in __init__ exercises binding-based dispatch in expression
# codegen. MIL hoist must install the constructor's namespace, otherwise
# ENUM/MODULE/IMPORTED_NAME/RECORD bindings silently fall through and crash.
from enum import Enum
import sys
from helper import Color


class E(Enum):
    A = 0
    B = 1


class Direct:
    e: E

    def __init__(self) -> None:
        self.e = E.A  # tpyc: ok


class FromModule:
    n: int

    def __init__(self) -> None:
        self.n = sys.argv.__len__()  # tpyc: ok


class FromImport:
    c: Color

    def __init__(self) -> None:
        self.c = Color.RED  # tpyc: ok


class Outer:
    class Kind(Enum):
        P = 0
        Q = 1


class FromNested:
    k: Outer.Kind

    def __init__(self) -> None:
        self.k = Outer.Kind.P  # tpyc: ok


def main() -> None:
    d = Direct()
    print(d.e)
    fm = FromModule()
    print(fm.n >= 1)
    fi = FromImport()
    print(fi.c)
    fn = FromNested()
    print(fn.k)


main()
