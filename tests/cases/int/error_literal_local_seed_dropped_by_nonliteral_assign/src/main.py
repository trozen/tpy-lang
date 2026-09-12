# Reassigning a literal-seeded local from a non-literal source clears the
# seed (see record_write in local_deduction.py); the local is then a
# regular int32 and a later uint64 use no longer retro-widens. Locks the
# behavior so a future change can't accidentally widen the retro-widen
# trigger to cover already-fully-resolved locals.
from tpy import uint64, int32


def get_int() -> int32:
    return int32(7)


def f(x: uint64) -> None:
    pass


def main() -> None:
    a = 0
    a = get_int()   # non-literal write -- seed dropped
    f(a)            # tpyc: error(/Type mismatch in argument 'x': expected uint64, got int32/)


main()
