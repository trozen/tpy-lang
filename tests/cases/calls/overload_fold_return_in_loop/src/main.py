# @overload dead-branch fold nested in a loop / dynamic `if` must not truncate
# the code reachable after that compound (the trailing return was dropped ->
# UB). `toplevel` is the inverse guard: a top-level fold still truncates.
from typing import overload


class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x


class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y


@overload
def in_loop(v: A, k: int) -> int: ...
@overload
def in_loop(v: B, k: int) -> int: ...
def in_loop(v: A | B, k: int) -> int:
    while k > 0:
        if isinstance(v, A):
            return v.x + k
        else:
            return v.y - k
    return 0            # reachable when k <= 0 -- must not be truncated


@overload
def in_branch(v: A, gate: bool) -> int: ...
@overload
def in_branch(v: B, gate: bool) -> int: ...
def in_branch(v: A | B, gate: bool) -> int:
    if gate:
        if isinstance(v, A):
            return v.x
        else:
            return v.y
    return -1           # reachable when gate is False -- must not be truncated


@overload
def toplevel(v: A) -> int: ...
@overload
def toplevel(v: B) -> int: ...
def toplevel(v: A | B) -> int:
    if isinstance(v, A):
        return v.x      # A-stub folds True here and truncates the tail below
    return v.y


def main() -> None:
    print(in_loop(A(5), 0))        # 0 -- loop skipped, trailing return
    print(in_loop(A(5), 3))        # 8
    print(in_loop(B(7), 0))        # 0
    print(in_loop(B(7), 2))        # 5
    print(in_branch(A(9), False))  # -1 -- branch skipped, trailing return
    print(in_branch(A(9), True))   # 9
    print(in_branch(B(4), False))  # -1
    print(in_branch(B(4), True))   # 4
    print(toplevel(A(2)))          # 2
    print(toplevel(B(6)))          # 6


main()
