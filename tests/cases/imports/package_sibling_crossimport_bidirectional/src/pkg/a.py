from tpy import int32

# pkg.a and pkg.b mutually cross-import (cycle peers under TPy's
# Python-cycle handling). Pkg/__init__.py re-exports from both.
from pkg.b import B


class A:
    def kind(self) -> int32:
        return int32(1)


def with_b() -> int32:
    b = B()
    return b.kind()
