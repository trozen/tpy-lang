# Error: unsafe_interior_mutable[...] is only valid on a class field declaration, not a
# parameter (it marks a field outside the owner's readonly boundary).
from tpy import int32, Ptr, unsafe_interior_mutable


def f(p: unsafe_interior_mutable[Ptr[int32]]) -> None:  # tpyc: error(/only valid on a class field/)
    pass


def main() -> None:
    pass


main()
