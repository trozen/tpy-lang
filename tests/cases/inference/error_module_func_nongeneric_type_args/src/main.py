# Type args on non-generic module function should be rejected.
from tpy import int32
import helpers

def main() -> None:
    y = helpers.inc[int32](int32(1))  # tpyc: error(/not generic/)

main()
