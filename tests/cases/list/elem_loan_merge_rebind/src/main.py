# An iteration's element loan whose index is uncertain. Rebinding `mid`
# retargets the iteration loan onto `outer` and merges it with `mid`'s own
# element loan; the merged loan keeps the element hop, takes the
# higher-ranked ELEMENT kind and loses the index, but the loop's iterator
# still holds it, so the write through `outer[j]` is reported as a possible
# hit on the element being iterated.
#
# The call passes the index of the element the loop does NOT iterate, so the
# warned write is the harmless leg of what the warning covers. `mid` is not
# read after the write: TPy's element assign overwrites the element in place
# while CPython rebinds the slot, so a read through the alias afterwards
# would diverge. Here the merged in-loop loan announces it. The STRAIGHT-LINE
# spelling of the same divergence (`mid = outer[1]`, then `outer[j] = ...`,
# no loop and no iteration loan) is silent --
# BUGS.md#element-alias-sees-element-assignment.
from tpy import int32

def f(outer: list[list[list[int32]]], j: int32) -> None:
    mid = outer[0]  # tpyc: ok
    for x in mid[0]:
        mid = outer[1]
        # the merged loan has no index, so the wording may not claim the hit
        outer[j] = []  # tpyc: warning(/may hit the element being iterated \(element assignment may invalidate the iterator\)/)
        print(x)

def main() -> None:
    f([[[1, 2]], [[3]]], 1)

main()
