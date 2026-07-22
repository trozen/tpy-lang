# tpy: ext_module
# A borrow-form __next__ return crosses as a WARNED COPY: the tp_iternext
# glue unwraps @error_return's val_or_ref borrow slot before marshalling
# (std::expected cannot hold T&, so the compiled method returns the value
# through the wrapper). Read-only iteration values match plain Python; the
# copy divergence itself is pinned ext-only in ext_checks.py. Own[...]
# returns move a fresh instance out (the quiet form).
from tpy import Int32, Own
from tpy.extern import export


@export
class Node:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v


@export
class Repeat:
    _cur: Node
    _n: Int32

    def __init__(self, v: Int32):
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
class Rows:
    _row: list[Int32]
    _n: Int32

    def __init__(self):
        self._row = [1, 2, 3]
        self._n = 0

    def __iter__(self) -> "Rows":
        return self

    def __next__(self) -> list[Int32]:
        if self._n >= 2:
            raise StopIteration
        self._n += 1
        return self._row


@export
class Fresh:
    _n: Int32

    def __init__(self):
        self._n = 0

    def __iter__(self) -> "Fresh":
        return self

    def __next__(self) -> Own[Node]:
        if self._n >= 3:
            raise StopIteration
        self._n += 1
        return Node(self._n * 10)
