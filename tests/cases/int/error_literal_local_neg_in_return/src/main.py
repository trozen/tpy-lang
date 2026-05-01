# Range diagnostic surfaces in RETURN context too, not only ARG: a
# negative literal returned as an unsigned type names the offending
# value rather than emitting the bare "got Int32" mismatch.
from tpy import UInt64


def f() -> UInt64:
    a = -1
    return a   # tpyc: error(/-1 assigned to 'a' is outside UInt64 range/)


def main() -> None:
    pass


main()
