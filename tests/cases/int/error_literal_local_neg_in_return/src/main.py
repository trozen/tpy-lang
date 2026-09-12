# Range diagnostic surfaces in RETURN context too, not only ARG: a
# negative literal returned as an unsigned type names the offending
# value rather than emitting the bare "got int32" mismatch.
from tpy import uint64


def f() -> uint64:
    a = -1
    return a   # tpyc: error(/-1 assigned to 'a' is outside uint64 range/)


def main() -> None:
    pass


main()
