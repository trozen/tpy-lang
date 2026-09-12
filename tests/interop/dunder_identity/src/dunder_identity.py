# tpy: ext_module
# Dunder slots thread identity/view candidates like method wrappers: a
# __getitem__ borrow return of `self` crosses as the SAME PyObject (incl.
# through an inherited slot, un-sliced), of a never-reassigned field as
# ONE registry-deduped borrow view (write-through), of a class-typed KEY
# as the key's own PyObject, and an arithmetic/unary dunder returning
# self/the operand crosses by identity through the nb_* slots -- matching
# plain Python's aliasing, so the driver asserts everything in parity.
from tpy import int32, readonly
from tpy.extern import export


@export
class Node:
    v: int32

    def __init__(self, v: int32):
        self.v = v


@export
class Grid:
    _cell: Node

    def __init__(self):
        self._cell = Node(5)

    def __getitem__(self, i: int32) -> Node:
        return self._cell

    def cell(self) -> Node:
        # Same field through a METHOD wrapper: the registry dedups across
        # emit-site kinds, so this is the SAME view PyObject as g[i].
        return self._cell


@export
class Echo:
    n: int32

    def __init__(self):
        self.n = 0

    def __getitem__(self, i: int32) -> "Echo":
        return self


@export
class EchoSub(Echo):
    # No own __getitem__: the base's mp_subscript slot is inherited, and
    # its identity path must hand back this derived instance un-sliced.
    pass


@export
class KeyEcho:
    def __init__(self):
        pass

    def __getitem__(self, k: Node) -> Node:
        return k


@export
class Acc:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __add__(self, o: "Acc") -> "Acc":
        return self if self.n >= o.n else o

    def __neg__(self) -> "Acc":
        return self


@export
class RoPick:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __add__(self, o: "RoPick") -> "readonly[RoPick]":
        # The readonly borrow spelling crosses identically to the plain
        # form (readonly is a TPy-side contract, a no-op at the boundary).
        return self
