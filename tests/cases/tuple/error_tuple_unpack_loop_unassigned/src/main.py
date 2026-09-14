# Error: a tuple-unpack target assigned only inside a loop body that sema
# cannot prove runs (the head reads a parameter) and read after the loop is
# rejected by definite-assignment, like the scalar `while ...: x = ...;
# print(x)` case. TPy is intentionally stricter here than CPython (which
# accepts it, raising UnboundLocalError only if the loop ran zero times). A
# head the value ranges prove (`i = 0; while i < 3`) is accepted instead:
# `while_unpack` in control_flow/hoist_nonvalue_read_after_loop.
from tpy import int32


def split2(s: str) -> tuple[str, str]:
    n = len(s) // 2
    return (s[:n], s[n:])


def run(rounds: int32) -> None:
    s = "abcdefghijklmnop"
    i = 0
    while i < rounds:
        s, kept = split2(s)
        i = i + 1
    print(kept)  # tpyc: error(/variable 'kept' may not be assigned/)


run(3)
