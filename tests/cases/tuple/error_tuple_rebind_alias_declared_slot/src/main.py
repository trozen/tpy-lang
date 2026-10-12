# A nested def rebinding an alias of an enclosing never-rebound owning tuple is
# checked against the declared slot where CPython aliases
# (BUGS.md#nested-def-alias-of-owning-tuple): a copy warning, and for @nocopy
# the "cannot copy" error, where the compile stops.
from tpy import Own, int32, nocopy


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class Tok:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def make_tok(n: int32, b: Box) -> tuple[Own[Tok], Box]:
    return (Tok(n), b)


def copyable(v: Box, c: bool) -> None:
    p = make_mixed(1, v)

    def k() -> int32:
        q = p
        # The rebind copies into the declared owned position.
        if c:
            q = p  # tpyc: warning(/copies tuple\[Own\[Box\], Box\] into owned storage; use copy\(\) to make this explicit/)
        return q[0].n
    print(k())


def non_copyable(v: Box, c: bool) -> None:
    p = make_tok(1, v)

    def k() -> int32:
        q = p
        # The rebind would copy a @nocopy element into the declared slot.
        if c:
            q = p  # tpyc: error(/cannot copy tuple\[Own\[Tok\], Box\] into owned storage of type 'Tok'; target is non-copyable/)
        return q[0].n
    print(k())


copyable(Box(3), True)
non_copyable(Box(3), True)
