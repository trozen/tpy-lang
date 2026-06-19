# Boundary inverse of the single-member implicit-else narrowing: ruling out one
# member of a 3-member union leaves a MULTI-member union, and direct member
# access must stay rejected -- even for a field common to every remaining member
# (which CPython allows). TPy requires narrowing to a single member first, so a
# fix that "optimized" this into a variant access would be a miscompile.
class A:
    def __init__(self, n: int): self.n = n
class B:
    def __init__(self, n: int): self.n = n
class C:
    def __init__(self, n: int): self.n = n

def common_field(v: A | B | C) -> int:
    if isinstance(v, A):
        return v.n + 1
    return v.n          # tpyc: error(/Cannot access field 'n' on type B \| C/)
