# The inverse of optional_ptr_local_own_slot: a pointer-repr Optional local
# passed at an `Own[record | None]` slot BEFORE its last use would need a
# copy, and that render is unwitnessed at this slot, so it keeps rejecting
# (the last-use occurrence moves).
from typing import Optional
from tpy import int32


class Pic:
    def __init__(self, n: int32) -> None:
        self.n = n


def main(c: bool) -> None:
    patches: list[Pic | None] = []
    patch: Pic | None = None
    if c:
        patch = Pic(1)
    patches.append(patch)  # tpyc: error(/method.arg_shape/)
    print(patch is None, len(patches))


main(True)
