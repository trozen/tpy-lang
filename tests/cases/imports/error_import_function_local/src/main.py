# Inferred MODULE-level bindings are exported; an inferred binding that is a
# function local is not, so importing it must still be rejected by name.
from tpy import int32
from lib import width, scratch  # tpyc: error(/'scratch' not found in module 'lib'/)


def main() -> int32:
    return width + scratch


main()
