# A module-level literal holding a module tuple name as a member stays
# refused: module storage owns by its layout, and this shape has no lowering
# (BUGS.md#module-tuple-name-member-rejects).
from tpy import int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


t = (A(1), 2)
u = (t, 1)  # tpyc: warning(/copies A into owned storage \(tuple element 0.0\)/) error(/decl.tuple_literal_shape/)
print(t[0].v, u[0][0].v)
