# Negative counterpart to return_mixed_safe_sources_ternary: when a
# ternary RHS has one safe arm (param-derived) but the other arm is a
# genuine local temporary, the recursive is_safe_to_return_expr must
# reject the assignment, and a later return of the local must be
# flagged dangling. This guards against a merge bug where one safe arm
# would falsely poll as "safe enough" for the whole ternary.

from tpy import int32


class Point:
    def __init__(self, x: int32) -> None:
        self.x = x


def ternary_unsafe_arm(seed: Point, flag: bool) -> Point:
    local = Point(0)
    result = seed if flag else local
    return result  # tpyc: error(/Cannot return local or temporary/)
