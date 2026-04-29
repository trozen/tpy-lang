# Phase 7 still rejects all subclass redeclarations of class constants;
# Phase 8 will relax shadow semantics for non-final ClassVar.
from typing import ClassVar
from tpy import Int32


class Parent:
    counter: ClassVar[Int32] = 0


class Child(Parent):
    counter: ClassVar[Int32] = 1  # tpyc: error(/cannot override class constant 'counter' from base 'Parent'/)
