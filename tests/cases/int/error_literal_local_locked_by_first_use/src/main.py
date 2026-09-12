# Conflicting demands on a literal-seeded local: the first ARG-position use
# locks the local to uint64; a subsequent int32 use surfaces the standard
# type-mismatch error PLUS a hint pointing at the locking call site.
from tpy import uint64, int32


def fu(x: uint64) -> None:
    pass


def fi(x: int32) -> None:
    pass


def main() -> None:
    a = 0
    fu(a)
    fi(a)   # tpyc: error(/'a' was promoted to 'uint64' by earlier use at line 17/)


main()
