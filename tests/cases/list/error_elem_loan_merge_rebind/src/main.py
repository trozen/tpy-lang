# The fourth borrow-invalidation wording: an element loan that is NOT an
# iteration and whose index is uncertain. Rebinding `mid` retargets the
# iteration loan onto `outer` and merges it with `mid`'s own element loan; the
# merged loan keeps the element hop, takes the higher-ranked ELEMENT kind and
# loses the index, so the write through `outer[j]` is reported as a possible
# hit on a BORROWED element rather than as an iteration in progress.
#
# An `error_` case because the rebound borrow-form local still rejects at
# lowering (BUGS.md#rebound-element-borrow-local-rejects -- valid Python the
# record and generator twins accept); diag.txt records the warning sema emitted
# before that error, which is the subject here.
from tpy import int32

def f(outer: list[list[list[int32]]], j: int32) -> None:
    mid = outer[0]  # tpyc: error(/not yet supported by C\+\+ code generation/)
    for x in mid[0]:
        mid = outer[1]
        # the merged loan has no index, so the wording may not claim the hit
        outer[j] = []  # tpyc: warning(/may hit a borrowed element \(element assignment may invalidate references\)/)
        print(x)

def main() -> None:
    f([[[1, 2]], [[3]]], 0)

main()
