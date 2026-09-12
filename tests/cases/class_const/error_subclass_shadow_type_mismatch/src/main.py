# Error: a child shadowing a parent's ClassVar must use the same type.
# Each class gets its own static slot, so the types are not subject to
# polymorphic compatibility checks -- but mixing types across the chain
# would surprise readers and prevent generic code from treating the
# shadowed slot uniformly. Reject.
from typing import ClassVar
from tpy import int32, int64


class Parent:
    counter: ClassVar[int32] = 0


class Child(Parent):
    counter: ClassVar[int64] = 0  # tpyc: error(/ClassVar 'counter' on 'Child' shadows base 'Parent.counter' with incompatible type 'int64' \(expected 'int32'\)/)
