# The resumable-frame twin of error_optional_ptr_local_copy_into_own_slot: a
# pointer-repr Optional frame local passed at an `Own[record | None]` slot
# BEFORE its last use would need a copy, which is unwitnessed at this slot,
# so the frame keeps the plain body's reject (the last-use occurrence moves).
from typing import Iterator, Optional
from tpy import int32


class Pic:
    def __init__(self, n: int32) -> None:
        self.n = n


def gen(c: bool) -> Iterator[int32]:
    patches: list[Pic | None] = []
    patch: Pic | None = None
    if c:
        patch = Pic(1)
    patches.append(patch)  # tpyc: error(/method.arg_shape/)
    yield len(patches)
    yield 0 if patch is None else patch.n


def main() -> None:
    for v in gen(True):
        print(v)


main()
