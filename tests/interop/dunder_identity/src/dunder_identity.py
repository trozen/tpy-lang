# tpy: ext_module
# Dunder slots thread identity/view candidates like method wrappers: a
# __getitem__ borrow return of `self` crosses as the SAME PyObject (incl.
# through an inherited slot, un-sliced) and of a never-reassigned field as
# ONE registry-deduped borrow view (write-through) -- matching plain
# Python's aliasing, so the driver asserts everything in parity. (A
# class-typed KEY returned from __getitem__ would cross by identity too,
# but the operator[] shim's const-return defect blocks that shape at the
# plain-TPy level -- see BUGS.md.)
from tpy import Int32
from tpy.extern import export


@export
class Node:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v


@export
class Grid:
    _cell: Node

    def __init__(self):
        self._cell = Node(5)

    def __getitem__(self, i: Int32) -> Node:
        return self._cell

    def cell(self) -> Node:
        # Same field through a METHOD wrapper: the registry dedups across
        # emit-site kinds, so this is the SAME view PyObject as g[i].
        return self._cell


@export
class Echo:
    n: Int32

    def __init__(self):
        self.n = 0

    def __getitem__(self, i: Int32) -> "Echo":
        return self


@export
class EchoSub(Echo):
    # No own __getitem__: the base's mp_subscript slot is inherited, and
    # its identity path must hand back this derived instance un-sliced.
    pass
