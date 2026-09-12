# The provenance walk that decides whether `__enter__` lends the manager's OWN
# storage, on the two arms reachable beyond a plain local hop: a body containing
# a nested def (which can rebind out of the walk's sight, forcing the
# conservative answer) and a body with two returns (every one must be checked).
#
# The walk's other binding arms cannot be reached from a borrow-returning
# `__enter__` at all -- sema rejects a for-loop binding, a `with ... as` binding
# and a global as "returning a local or temporary as reference" -- so they stay
# uncovered by construction rather than by omission.
#
# Both managers lend their own storage, so both must be kept alive for the
# mutating reads after the block; a copy or an early drop shows a wrong total.
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32):
        self.n = n


class ViaNestedDef:
    inner: Item

    def __init__(self):
        self.inner = Item(3)

    def __enter__(self) -> Item:
        def tag() -> int32:
            return 7

        picked = self.inner
        return picked

    def __exit__(self, et, ev, tb) -> None:
        pass


class ViaTwoReturns:
    inner: Item

    def __init__(self):
        self.inner = Item(4)

    def __enter__(self) -> Item:
        if self.inner.n > 0:
            hop = self.inner  # one arm hops through a local ...
            return hop
        return self.inner  # ... the other is direct; both must be checked

    def __exit__(self, et, ev, tb) -> None:
        pass


def read_nested(flag: bool) -> int32:
    if flag:
        with ViaNestedDef() as c:
            pass
    else:
        with ViaNestedDef() as c:
            pass
    c.n += 10
    return c.n


def read_two_returns(flag: bool) -> int32:
    if flag:
        with ViaTwoReturns() as d:
            pass
    else:
        with ViaTwoReturns() as d:
            pass
    d.n += 10
    return d.n


def main() -> None:
    print(read_nested(True))
    print(read_two_returns(True))


main()
