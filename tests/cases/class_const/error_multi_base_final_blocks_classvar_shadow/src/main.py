# Multi-base inheritance: a Final declaration on *any* ancestor blocks
# the child's shadow, regardless of BFS order. Here A.X is Final and
# B.X is ClassVar; Child's shadow attempt is rejected by A's Final.
from typing import ClassVar, Final
from tpy import int32


class A:
    X: Final[int32] = 1


class B:
    X: ClassVar[int32] = 2


class C(A, B):
    X: ClassVar[int32] = 3  # tpyc: error(/cannot override Final class constant 'X' from base 'A'/)
