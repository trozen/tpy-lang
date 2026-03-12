# Regression: non-value locals (rvalue-init) declared independently in both branches
# of an if/else, used only within their branch, must not share pointer-local slots.
# Before the fix, then-branch slot info leaked into the else-branch context, causing
# the else-branch to generate a dereference of an out-of-scope pointer.
from tpy import Int32


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


def branch_rvalue_independent(cond: bool) -> None:
    # p is rvalue-init in both branches, used only within each branch (not after)
    if cond:
        p = Point(1, 2)
        print(p.x, p.y)
    else:
        p = Point(3, 4)
        print(p.x, p.y)


def branch_rvalue_three_way(flag: Int32) -> None:
    # Three-way: each elif/else branch has its own independent local
    if flag == 0:
        p = Point(10, 20)
        print(p.x, p.y)
    elif flag == 1:
        p = Point(30, 40)
        print(p.x, p.y)
    else:
        p = Point(50, 60)
        print(p.x, p.y)


def branch_mixed_scope(cond: bool) -> None:
    # First var is used after (pre-declared), second is branch-only (independent)
    if cond:
        shared = Point(1, 2)
        local = Point(10, 20)
        print(local.x, local.y)
    else:
        shared = Point(3, 4)
        local = Point(30, 40)
        print(local.x, local.y)
    print(shared.x, shared.y)


branch_rvalue_independent(True)
branch_rvalue_independent(False)
branch_rvalue_three_way(0)
branch_rvalue_three_way(1)
branch_rvalue_three_way(2)
branch_mixed_scope(True)
branch_mixed_scope(False)
