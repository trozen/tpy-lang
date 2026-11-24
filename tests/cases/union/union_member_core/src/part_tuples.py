# Tuples of unions: each element converts at its own member; an element
# its last read moves moves, any other is copied, where the twin's
# `return t;` moves the whole tuple
# (BUGS.md#dying-owned-value-staged-result-copies). (No caller consumes a
# tuple-of-union result yet,
# BUGS.md#value-union-tuple-element-reads-reject: the bodies' C++ is the
# subject, built by the exec phase. A union whose member is a tuple and a
# nested tuple literal of unions are refused or ill-formed,
# BUGS.md#union-member-fresh-and-tuple-literal-shapes-reject.)
from tpy import Own, int32, int64, uint32, uint64


class A:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n]


# a named tuple's record element its last read moves moves into its
# member
def rec_union(k: int32) -> tuple[Own[A | int32], int32]:
    t = (A(k), k)
    return t  # tpyc: ok


def rec_twin(k: int32) -> tuple[Own[A], int32]:
    t = (A(k), k)
    return t  # tpyc: ok


# a named tuple's owned str is copied into its member, flat and nested
# (a value, which no element read moves), where the twin's `return v;`
# moves the tuple
def str_union(s: Own[str]) -> tuple[str | int32]:
    v = (s,)
    return v  # tpyc: ok


def str_nested(s: Own[str]) -> tuple[tuple[str | int32]]:
    v = ((s,),)
    return v  # tpyc: ok


def str_twin(s: Own[str]) -> tuple[str]:
    v = (s,)
    return v  # tpyc: ok


# a tuple literal's element lands in its member
def lit_union(n: uint32) -> tuple[int64 | uint64, int32]:
    return (n, 1)  # tpyc: ok


def main() -> None:
    a, k = rec_twin(2)
    print("tuple-twin", len(a.xs), k, str_twin("s")[0])
