# Aug-assigning a module global from a body that does NOT declare it `global`:
# the target is not a declared local, so the eligible-scalar in-place form does
# not admit it. The `global`-declared sibling is pinned by
# tests/cases/globals/global_unannotated_decl.
from tpy import Int32

counter: Int32 = 10


def bump() -> Int32:
    counter += 1  # tpyc: error(/stmt\.aug_assign/)
    return counter


def main() -> None:
    print(bump())


main()
