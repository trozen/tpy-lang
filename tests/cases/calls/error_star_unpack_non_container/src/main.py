# A non-container expression cannot be *unpacked into a vararg pack.
# The diagnostic fires from `_unpack_star_element_type` in
# `tpyc/sema/expressions.py` (reached via `analyze_call_arg` on the
# generic-call inference path).
from tpy import int32, nocopy


@nocopy
class Box[T]:
    val: T

    def __init__(self, v: T) -> None:
        self.val = v


def take_all[T](*boxes: Box[T]) -> int32:
    n: int32 = 0
    for b in boxes:
        n += 1
    return n


def main() -> None:
    take_all(*42)  # tpyc: error(/Cannot unpack type/)


main()
