# Error: interior[...] is only valid on a class field declaration, not a
# parameter (it marks a field outside the owner's readonly boundary).
from tpy import Int32, Ptr, interior


def f(p: interior[Ptr[Int32]]) -> None:  # tpyc: error(/only valid on a class field/)
    pass


def main() -> None:
    pass


main()
