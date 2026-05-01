# A literal-seeded local can be locked to a fixed-int target by *any*
# typed-slot use, not just an ARG-position call. Here the lock comes from
# an INIT to an annotated UInt64 local; a subsequent Int32-arg use surfaces
# the same "promoted by earlier use" hint as the ARG-locking case.
from tpy import UInt64, Int32


def fi(x: Int32) -> None:
    pass


def main() -> None:
    a = 0
    y: UInt64 = a   # locks a to UInt64 here
    fi(a)   # tpyc: error(/'a' was promoted to 'UInt64' by earlier use at line 14/)


main()
