# Conflicting demands on a literal-seeded local: the first ARG-position use
# locks the local to UInt64; a subsequent Int32 use surfaces the standard
# type-mismatch error PLUS a hint pointing at the locking call site.
from tpy import UInt64, Int32


def fu(x: UInt64) -> None:
    pass


def fi(x: Int32) -> None:
    pass


def main() -> None:
    a = 0
    fu(a)
    fi(a)   # tpyc: error(/'a' was promoted to 'UInt64' by earlier use at line 17/)


main()
