# A `from tpy import` naming no export is rejected at that alias's own line
# (same wording as a user module's missing name).
from tpy import (
    int32,
    Int32,  # tpyc: error(/'Int32' not found in module 'tpy'/)
)


def main() -> None:
    x: int32 = 1
    print(x)


main()
