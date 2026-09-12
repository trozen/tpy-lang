# tpy: ext_module
# Borrow-form __next__ returns cross ALIASING, like plain Python: the
# tp_iternext slot unwraps @error_return's val_or_ref borrow slot
# (std::expected cannot hold T&) and threads the receiver candidate, so a
# never-reassigned field source yields ONE registry-deduped borrow view
# (identity + write-through; asserted in the parity driver -- plain Python
# aliases identically). A borrow LIST return still copies (warned; no view
# path for containers) -- that divergence is pinned ext-only in
# ext_checks.py. Own[...] returns move a fresh instance out.
from tpy import int32, Own, readonly
from tpy.extern import export


@export
class Node:
    v: int32

    def __init__(self, v: int32):
        self.v = v


@export
class Repeat:
    _cur: Node
    _n: int32

    def __init__(self, v: int32):
        self._cur = Node(v)
        self._n = 0

    def __iter__(self) -> "Repeat":
        return self

    def __next__(self) -> Node:
        if self._n >= 3:
            raise StopIteration
        self._n += 1
        return self._cur


@export
class RepeatSub(Repeat):
    # No own __next__/__iter__: the base's tp_iternext slot is inherited,
    # and its view path must alias the field of THIS derived instance.
    pass


@export
class Peek:
    _cur: Node
    _n: int32

    def __init__(self, v: int32):
        self._cur = Node(v)
        self._n = 0

    def __iter__(self) -> "Peek":
        return self

    def __next__(self) -> "readonly[Node]":
        # The readonly borrow spelling crosses identically to the plain
        # form (readonly is a TPy-side contract, a no-op at the boundary
        # and in the lib/cpy stubs).
        if self._n >= 2:
            raise StopIteration
        self._n += 1
        return self._cur


@export
class Rows:
    _row: list[int32]
    _n: int32

    def __init__(self):
        self._row = [1, 2, 3]
        self._n = 0

    def __iter__(self) -> "Rows":
        return self

    def __next__(self) -> list[int32]:
        if self._n >= 2:
            raise StopIteration
        self._n += 1
        return self._row


@export
class Fresh:
    _n: int32

    def __init__(self):
        self._n = 0

    def __iter__(self) -> "Fresh":
        return self

    def __next__(self) -> Own[Node]:
        if self._n >= 3:
            raise StopIteration
        self._n += 1
        return Node(self._n * 10)
