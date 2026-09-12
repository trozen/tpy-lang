# A tuple-unpack for-head whose first target already exists outside the loop:
# the rebind has no loop-scoped binding to declare, so the head rejects. The
# fresh-target sibling is pinned by
# tests/cases/iterators/for_tuple_unpack_targets.
from tpy import int32


def reused_target(ps: list[tuple[int32, int32]]) -> int32:
    a = 100
    s = 0
    for a, b in ps:  # tpyc: error(/tuple\.reused_target/)
        s = s + a + b
    return s + a


def main() -> None:
    ps = [(1, 2), (3, 4)]
    print(reused_target(ps))


main()
