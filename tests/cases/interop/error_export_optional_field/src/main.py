# An Optional REFERENCE-class field cannot be a getset: a view of it would
# alias the storage slot across a None/value rebind, and the never-reassigned
# case a bare class field crosses on is not tracked through the gate --
# rejected with that reasoning (the value form, `Optional[int32]` and
# friends, crosses as a getset).
# tpy: ext_module
from typing import Optional
from tpy import int32
from tpy.extern import export


@export
class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@export
class Tree:
    root: Optional[Node]  # tpyc: error(/field 'root' of type 'Node \| None' cannot be exposed as a getset field: a view of an Optional reference-class field is not supported yet/)

    def __init__(self) -> None:
        self.root = None
