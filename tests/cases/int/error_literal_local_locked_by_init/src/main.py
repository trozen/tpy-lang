# A literal-seeded local can be locked to a fixed-int target by *any*
# typed-slot use, not just an ARG-position call. Here the lock comes from
# an INIT to an annotated uint64 local; a subsequent int32-arg use surfaces
# the same "promoted by earlier use" hint as the ARG-locking case.
from tpy import uint64, int32


def fi(x: int32) -> None:
    pass


def main() -> None:
    a = 0
    y: uint64 = a   # locks a to uint64 here
    fi(a)   # tpyc: error(/'a' was promoted to 'uint64' by earlier use at line 14/)


main()
