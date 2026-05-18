from tpy import Int32

# pkg.a and pkg.b mutually cross-import (cycle peers under TPy's
# Python-cycle handling). Pkg/__init__.py re-exports from both.
from pkg.b import B


class A:
    def kind(self) -> Int32:
        return Int32(1)


def with_b() -> Int32:
    b = B()
    return b.kind()
