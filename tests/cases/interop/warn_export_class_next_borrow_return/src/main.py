# Borrow-form __next__ returns: the tp_iternext slot threads the receiver
# candidate (after unwrapping @error_return's val_or_ref borrow slot, since
# std::expected cannot hold T&), so a never-reassigned field source -- plain
# or readonly[...] spelled -- crosses as an aliasing borrow view and stays
# quiet; a reassignable-field source and a borrow list return still copy
# and warn. Own[...] moves a fresh instance out (the quiet form). Runtime
# aliasing is pinned in tests/interop/next_borrow.
# tpy: ext_module
from tpy import Int32, Own, readonly
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

    def __init__(self):
        self._cur = Node(0)
        self._n = 0

    def __iter__(self) -> "Repeat":  # tpyc: ok
        return self

    def __next__(self) -> Node:
        if self._n >= 3:
            raise StopIteration
        self._n += 1
        return self._cur  # tpyc: ok


@export
class Rows:
    _row: list[Int32]
    _n: Int32

    def __init__(self):
        self._row = [1, 2]
        self._n = 0

    def __next__(self) -> list[Int32]:
        if self._n >= 2:
            raise StopIteration
        self._n += 1
        return self._row  # tpyc: warning(/'__next__': returns a list by reference/)


@export
class Peek:
    _cur: Node
    _n: Int32

    def __init__(self):
        self._cur = Node(0)
        self._n = 0

    def __next__(self) -> "readonly[Node]":
        if self._n >= 3:
            raise StopIteration
        self._n += 1
        return self._cur  # tpyc: ok


@export
class Swapping:
    _cur: Node
    _n: Int32

    def __init__(self):
        self._cur = Node(0)
        self._n = 0

    def reset(self) -> None:
        self._cur = Node(1)

    def __next__(self) -> Node:
        if self._n >= 3:
            raise StopIteration
        self._n += 1
        return self._cur  # tpyc: warning(/'__next__': returns exposed class 'Node' by reference from a source with no live object behind it/)


@export
class Fresh:
    _n: Int32

    def __init__(self):
        self._n = 0

    def __next__(self) -> Own[Node]:  # tpyc: ok
        if self._n >= 3:
            raise StopIteration
        self._n += 1
        return Node(self._n)
