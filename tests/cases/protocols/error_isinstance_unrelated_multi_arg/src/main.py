# Cross-protocol isinstance to a multi-arg protocol that isn't a parent of
# the source protocol must be rejected at sema. Without the guard, codegen
# would emit `Sequence<T_it>` (one template arg) for a concept signature
# `template<typename T, typename ElemT>`, causing a cryptic C++ error.
#
# Sequence is not a parent of Iterable, so its element type can't be
# derived from `Iterable[Int32]`. Use `it: Sequence[Int32]` directly if
# indexed access is needed.
from typing import Iterable, Sequence
from tpy import Int32


def test(it: Iterable[Int32]) -> None:
    if isinstance(it, Sequence):  # tpyc: error(/requires 'Sequence' to inherit from 'Iterable'/)
        pass


def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    test(nums)


main()
