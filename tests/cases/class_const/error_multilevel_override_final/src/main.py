# Error: ancestor walk catches transitive overrides, not just direct bases.
# (Without the walk, B contributes no class_constants and C's redeclaration
# would silently shadow A's, emitting two `static constexpr X` members.)
from typing import Final
from tpy import Int32


class A:
    X: Final[Int32] = 1


class B(A):
    pass


class C(B):
    X: Final[Int32] = 2  # tpyc: error(/cannot override Final class constant 'X' from base 'A'/)
