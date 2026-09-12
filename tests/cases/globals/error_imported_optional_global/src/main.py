# An IMPORTED value-optional global read under a narrow: unlike its same-module
# twin it is never seeded, so the narrowed read has no qualified deref render
# and the body rejects.
from tpy import int32
from helper import maybe


def read_maybe() -> int32:
    if maybe is not None:  # tpyc: error(/optional_other_nonetype/)
        return maybe
    return 0


def main() -> None:
    print(read_maybe())


main()
