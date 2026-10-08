# A borrow local bound from an optional-returning __getitem__ / auto_readonly
# method call inside an INFERRED-readonly method must be const: sema resolves
# the mutable twin (readonly-ness is inferred after body analysis), but the
# const receiver makes C++ pick the const twin returning const T*. Also pins
# the healthy paths: an explicit readonly[T]-param receiver, and a MUTATING
# method whose bound borrow stays non-const and is written through (the
# no-over-trigger inverse; the write is observed by the readers). read_call
# pins the routable method-call-on-const-rooted-receiver arm (no Optional, so
# it lowers through THIR and is byte-diffed on both codegen paths).
from tpy import int32, auto_readonly, readonly


class Cell:
    v: int32

    def __init__(self):
        self.v = 7


class Store:
    _cell: Cell
    _present: bool

    def __init__(self):
        self._cell = Cell()
        self._present = True

    def __getitem__(self, k: int32) -> Cell | None:
        if self._present:
            return self._cell
        return None

    @auto_readonly
    def get(self, k: int32) -> auto_readonly[Cell | None]:
        if self._present:
            return self._cell
        return None

    @auto_readonly
    def first(self) -> auto_readonly[Cell]:
        return self._cell


class Outer:
    store: Store

    def __init__(self):
        self.store = Store()

    def read_sub(self) -> int32:
        # Inferred readonly; subscript resolves the const twin at C++ level.
        p = self.store[5]
        if p is not None:
            return p.v
        return -1

    def read_named(self) -> int32:
        # Inferred readonly like read_sub: the NAMED accessor result is
        # only read, so its loan on the receiver credits nothing and the
        # const twin binds -- pins the symmetry with read_sub.
        p = self.store.get(5)
        if p is not None:
            return p.v
        return -1

    def bump(self) -> None:
        # Mutating sibling: the bound borrow stays non-const and is written
        # through -- proves the const arm does not over-trigger.
        p = self.store[5]
        if p is not None:
            p.v = p.v + 1


def read_param(s: readonly[Store]) -> int32:
    # The pre-existing healthy path: declared-readonly receiver.
    p = s[5]
    if p is not None:
        return p.v
    return -1


def read_call(o: readonly[Outer]) -> int32:
    # A method call on a const-rooted receiver (`s` aliases a field off the
    # readonly param) returning a bare reference type binds `const Cell&`.
    # This body has no Optional, so it routes through THIR -- exercising the
    # const-rooted method-call arm on both codegen paths, not just the AST.
    s = o.store
    c = s.first()
    return c.v


def main() -> None:
    d = Outer()
    print(d.read_sub())
    print(d.read_named())
    d.bump()
    print(d.read_sub())
    print(read_param(d.store))
    print(read_call(d))


main()
