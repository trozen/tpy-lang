# The fence beside `list/reassigned_container_alias`: a READONLY container
# source has no const pointer form at the reseat, so the reassigned alias must
# keep rejecting rather than bind a mutable `std::vector<int32_t>*` to it.
from tpy import int32, readonly


def pick(a: readonly[list[int32]], b: readonly[list[int32]]) -> None:
    x = a
    x = b  # tpyc: error(/not yet supported by C\+\+ code generation/)
    print(len(x))


def main() -> None:
    pick([1], [2, 2])


main()
