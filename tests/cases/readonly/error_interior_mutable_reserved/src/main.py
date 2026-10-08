# `unsafe_interior_mutable` on a non-Ptr field is reserved for an inline
# field that stays mutable through a readonly owner, which does not exist yet.
from tpy import int32, unsafe_interior_mutable


class Counter:
    hits: unsafe_interior_mutable[list[int32]]  # tpyc: error(/is not supported: the marker is reserved/)

    def __init__(self) -> None:
        self.hits = []


def main() -> None:
    pass


main()
