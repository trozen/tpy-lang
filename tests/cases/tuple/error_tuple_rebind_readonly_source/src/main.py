# A rebound pointer tuple bound from a declared read-only source beside a
# binding owning an element: a binding owning nothing points only into storage
# the body may write, so a read-only parameter, and the receiver of a
# '@readonly' method or of an '@auto_readonly' one's const clone, are refused
# at the binding (BUGS.md#ptr-tuple-repoint-root-rejects).
from tpy import Own, auto_readonly, int32, readonly


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    bp: tuple[Box, Box]

    def __init__(self) -> None:
        self.bp = (Box(50), Box(60))

    @readonly
    def readonly_self(self) -> int32:
        t = (Box(1), Box(2))
        t = (Box(3), Box(4))
        # readonly-self: the method's receiver is read-only.
        t = self.bp  # tpyc: error(/'t' keeps objects of its own in storage of 'readonly_self', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'self', the receiver of a readonly method; bind this value to a name of its own/)
        return t[0].n

    @auto_readonly
    def auto_self(self) -> int32:
        t = (Box(1), Box(2))
        t = (Box(3), Box(4))
        # auto-readonly-self: the const clone's receiver is read-only.
        t = self.bp  # tpyc: error(/'t' keeps objects of its own in storage of 'auto_self', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'self', the receiver of a readonly method; bind this value to a name of its own/)
        return t[0].n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def readonly_source(h: readonly[H], v: Box) -> None:
    t = make_mixed(1, v)
    # readonly-param: the parameter is declared read-only.
    t = h.bp  # tpyc: error(/'t' keeps objects of its own in storage of 'readonly_source', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'h', a readonly parameter; bind this value to a name of its own/)
    print(t[0].n)


readonly_source(H(), Box(1))
print(H().readonly_self())
print(H().auto_self())
