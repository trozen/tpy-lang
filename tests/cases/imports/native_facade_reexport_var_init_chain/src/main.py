# Native package init re-exporting a non-Final module-level variable whose
# initializer is a function call (so it cannot fold at compile time and
# requires the source module's __tpy_init() to run). Pre-fix, the init was
# never chained through the native facade and the variable read as
# default-initialized (0); this test pins the chain.
from pkg import COUNTER
from tpy import Int32


def main() -> None:
    print(COUNTER)
    print(COUNTER + Int32(1))


main()
