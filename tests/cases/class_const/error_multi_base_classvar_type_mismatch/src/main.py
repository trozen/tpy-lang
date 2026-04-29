# Multi-base inheritance: Child must satisfy *every* declaring ancestor's
# type, not just the first one matched by BFS. Here A.X is Int32 and B.X
# is Int64; Child's Int32 declaration matches A but not B -- reject.
from typing import ClassVar
from tpy import Int32, Int64


class A:
    X: ClassVar[Int32] = 0


class B:
    X: ClassVar[Int64] = 0


class C(A, B):
    X: ClassVar[Int32] = 0  # tpyc: error(/ClassVar 'X' on 'C' shadows base 'B.X' with incompatible type 'Int32' \(expected 'Int64'\)/)
