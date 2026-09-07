# Comparing two locals of a NON-generic recursive union alias: the compare arm
# is keyed on the generic alias instance, so the plain wrapper pair has no
# admitted slot type, so `a == b` is rejected.
from tpy import Int32

type Json = None | bool | Int32 | str | list[Json]


def main() -> None:
    a: Json = Int32(1)  # tpyc: error(/decl\.slot_type/)
    b: Json = Int32(1)
    print(a == b)


main()
