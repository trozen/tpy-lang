# Multi-base inheritance: Child must satisfy *every* declaring ancestor's
# type, not just the first one matched by BFS. Here A.X is int32 and B.X
# is int64; Child's int32 declaration matches A but not B -- reject.
from typing import ClassVar
from tpy import int32, int64


class A:
    X: ClassVar[int32] = 0


class B:
    X: ClassVar[int64] = 0


class C(A, B):
    X: ClassVar[int32] = 0  # tpyc: error(/ClassVar 'X' on 'C' shadows base 'B.X' with incompatible type 'int32' \(expected 'int64'\)/)
