# Aug-assigning a module global from a body that does NOT declare it `global`:
# the augmented assignment makes `counter` the body's local, which it reads
# before any binding (CPython's UnboundLocalError). The `global`-declared
# sibling is pinned by tests/cases/globals/global_unannotated_decl.
from tpy import int32

counter: int32 = 10


def bump() -> int32:
    counter += 1  # tpyc: error(/'counter' is modified before it is bound on line 11: .* does not see the module's 'counter'/)
    return counter


def main() -> None:
    print(bump())


main()
