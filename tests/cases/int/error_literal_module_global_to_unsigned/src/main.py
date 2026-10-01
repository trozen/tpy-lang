# A module global initialized from an integer literal has the default int
# type, and a use never decides a type: passing it to a uint64 parameter
# is a type mismatch (TODO.md "Polymorphic integer literals").
from tpy import uint64


CONST = 5


def f(x: uint64) -> None:
    pass


def main() -> None:
    f(CONST)   # tpyc: error(/Type mismatch in argument 'x': expected uint64, got int32/)


main()
