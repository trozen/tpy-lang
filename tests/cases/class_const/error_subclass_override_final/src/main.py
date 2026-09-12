# Error: a subclass cannot override a parent's Final class constant in v1.
# (Phase 8 will allow shadowing for non-final ClassVar.)
from typing import Final
from tpy import int32


class Parent:
    LIMIT: Final[int32] = 10


class Child(Parent):
    LIMIT: Final[int32] = 20  # tpyc: error(/cannot override Final class constant 'LIMIT' from base 'Parent'/)
